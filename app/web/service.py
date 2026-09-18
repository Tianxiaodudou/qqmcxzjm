"""QQMusicApi 服务封装：客户端生命周期、凭证刷新、登录与业务调用。"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import re
import time
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from qqmusic_api import Client
from qqmusic_api.models.login import PhoneLoginEvents, QR, QRCodeLoginEvents, QRLoginType
from qqmusic_api.models.request import Credential
from qqmusic_api.modules.search import SearchType
from qqmusic_api.modules.song import EncryptedSongFileType, SongFileInfo, SongFileType

from . import env, errors, security, store
from .blocks import BlocksMixin

logger = logging.getLogger("qqmusic.service")

T = TypeVar("T")

_QR_TYPES = {"qq": QRLoginType.QQ, "wx": QRLoginType.WX, "mobile": QRLoginType.MOBILE}
_SESSION_TTL = 300
_HTML_TAG_RE = re.compile(r"<[^>]{0,40}>")


def service_field(obj: Any, *keys: str, default: Any = "") -> Any:
    """从模型或字典里按候选字段名取第一个非空值。"""
    for key in keys:
        if obj is None:
            return default
        value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if value not in (None, "", []):
            return value
    return default


def clean_text(value: Any) -> str:
    """清理上游返回文本：去掉搜索高亮标签（<em>）与多余空白。"""
    text = str(value if value is not None else "")
    text = _HTML_TAG_RE.sub("", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').strip()


class QQService(BlocksMixin):
    """唯一的 SDK 客户端持有者，负责登录态与凭证刷新。"""

    def __init__(self) -> None:
        self._client: Client | None = None
        self._lock = asyncio.Lock()
        self._sessions: dict[str, QR] = {}
        self._sessions_created: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 客户端与凭证
    # ------------------------------------------------------------------
    def _credential_object(self) -> Credential | None:
        data = store.load_credentials()
        if not data:
            return None
        try:
            return Credential.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("凭证解析失败，已忽略：%s", security.sanitize_log(str(exc)))
            return None

    async def client(self) -> Client:
        async with self._lock:
            if self._client is None:
                self._client = Client(credential=self._credential_object() or None)
            return self._client

    def _persist_credential(self, credential: Credential) -> None:
        """凭证只落盘在后端，权限 600，永不返回给前端。"""
        data = credential.model_dump(by_alias=True, exclude_none=True, mode="json")
        store.save_credentials(data)
        if self._client is not None:
            self._client.credential = credential

    def reload_credential(self) -> None:
        if self._client is not None:
            self._client.credential = self._credential_object()

    async def refresh(self) -> bool:
        """仅在接口明确提示凭证失效时调用一次，不做轮询。"""
        if not store.load_credentials():
            return False
        client = await self.client()
        try:
            credential = await client.login.refresh_credential()
        except Exception as exc:  # noqa: BLE001
            logger.info("凭证刷新失败：%s", security.sanitize_log(str(exc)))
            return False
        if not credential:
            return False
        self._persist_credential(credential)
        logger.info("凭证刷新成功")
        return True

    async def call(self, handler: Callable[[Client], Awaitable[T]]) -> T:
        """统一调用入口：凭证过期时刷新并重试一次，失败则要求重新登录。"""
        client = await self.client()
        try:
            return await handler(client)
        except Exception as exc:  # noqa: BLE001
            kind = errors.classify(exc)
            if kind == errors.RATELIMITED:
                raise errors.RatelimitedAppError() from exc
            if kind != errors.CREDENTIAL_EXPIRED:
                raise
            logger.info("检测到凭证失效，尝试自动刷新")
            if await self.refresh():
                try:
                    return await handler(client)
                except Exception as retry_exc:  # noqa: BLE001
                    if errors.classify(retry_exc) == errors.CREDENTIAL_EXPIRED:
                        store.clear_credentials()
                        raise errors.LoginExpiredError("登录已过期，请重新登录") from retry_exc
                    if errors.classify(retry_exc) == errors.RATELIMITED:
                        raise errors.RatelimitedAppError() from retry_exc
                    raise
            store.clear_credentials()
            raise errors.LoginExpiredError("登录已过期，请重新登录") from exc

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        closer = getattr(client, "close", None) or getattr(client, "aclose", None)
        if not closer:
            return
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # noqa: BLE001
            logger.info("关闭客户端失败：%s", security.sanitize_log(str(exc)))

    # ------------------------------------------------------------------
    # 登录态
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {"logged_in": store.is_logged_in()}

    async def logout(self, remove_account: bool = True) -> None:
        """退出登录：默认把该账号从账号列表里移除（多账号场景）。"""
        store.clear_credentials(remove_account=remove_account)
        if self._client is not None:
            self._client.credential = None
        self._sessions.clear()
        self._sessions_created.clear()

    def list_accounts(self) -> list[dict[str, Any]]:
        """所有已登录过的账号（不含凭证本体）。"""
        return store.account_list()

    async def switch_account(self, key: str) -> dict[str, Any]:
        """切换当前账号：账号各自的登录态都保留在本地。"""
        # 有进行中的任务时不允许切换：下载用的是当前账号的登录态，
        # 中途换账号会让正在排队的任务拿到别人的凭证（或直接失败）。
        from .context import manager as _manager  # 延迟导入，避免与 context 循环依赖

        active = _manager.active_count()
        if active:
            raise errors.BadRequestError(f"还有 {active} 个任务正在进行，请先「全部暂停」再切换账号")
        credential = store.switch_account(str(key or ""))
        if not credential:
            raise errors.BadRequestError("账号不存在或登录态已失效，请重新登录")
        try:
            parsed = Credential.model_validate(credential)
        except Exception as exc:  # noqa: BLE001
            raise errors.BadRequestError("该账号登录态已失效，请重新登录") from exc
        if self._client is not None:
            self._client.credential = parsed
        self._sessions.clear()
        self._sessions_created.clear()
        return {"logged_in": True, "musicid": credential.get("musicid") or ""}

    @staticmethod
    def _vip_summary(vip: Any) -> dict[str, Any]:
        """把会员接口结果整理成「会员等级 / 会员时长」。"""
        identity = service_field(vip, "identity", default=None)
        userinfo = service_field(vip, "userinfo", default=None)
        labels: list[str] = []
        for flag, label in (
            ("svip", "超级会员"),
            ("huge_vip", "豪华绿钻"),
            ("vip", "绿钻"),
            ("twelve", "十二平台会员"),
            ("year_flag", "年费绿钻"),
            ("huge_year_flag", "豪华年费绿钻"),
            ("star", "星级会员"),
            ("ystar", "年费星级会员"),
        ):
            if service_field(identity, flag, default=0):
                labels.append(label)
        level = service_field(identity, "level", default=0) or service_field(userinfo, "music_level", default=0)

        def _stamp(value: Any) -> int:
            """把「到期时间」统一成秒级时间戳。

            各接口给的形式不统一：秒级时间戳、毫秒级时间戳、``2026-01-01`` 这类日期
            字符串。认不出来就当没有（0），不因为解析失败丢掉整个会员信息。
            """
            if value in (None, "", 0, "0"):
                return 0
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return 0
                if not text.isdigit():
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
                        try:
                            return int(time.mktime(time.strptime(text, fmt)))
                        except ValueError:
                            continue
                    return 0
                value = text
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                return 0
            if number > 10**12:      # 毫秒级
                number //= 1000
            return number if number > 10**9 else 0

        # 豪华绿钻 / 十二平台 / 家庭组 / 情侣 / 八平台 / 星级 各有到期字段，
        # 取最晚的那个当「会员剩余时长」，避免只认 userinfo.expire 而漏掉真正的到期日。
        stamp = max(
            _stamp(service_field(userinfo, "expire", default=0)),
            _stamp(service_field(identity, "huge_vip_end", default="")),
            _stamp(service_field(identity, "twelve_end", default="")),
            _stamp(service_field(identity, "group_vip_end", default="")),
            _stamp(service_field(identity, "cp_lover_end", default="")),
            _stamp(service_field(identity, "eight_end", default="")),
            _stamp(service_field(vip, "star_end", default="")),
            _stamp(service_field(vip, "ystar_end", default="")),
        )
        expire_at = ""
        days_left = 0
        if stamp > 0:
            expire_at = time.strftime("%Y-%m-%d", time.localtime(stamp))
            days_left = max(int((stamp - time.time()) // 86400), 0)
        level_text = " ".join(labels) if labels else "非会员"
        if level:
            level_text = f"{level_text} LV{level}"
        desc = ""
        if expire_at:
            desc = f"有效期至 {expire_at}（剩余 {days_left} 天）"
        elif labels:
            desc = "长期有效"
        else:
            desc = "未开通会员"
        return {"vip_level": level_text, "vip_desc": desc, "vip_expire": expire_at, "vip_days_left": days_left}

    async def account_info(self, refresh: bool = True) -> dict[str, Any]:
        """账号信息 + 会员等级 + 会员时长（refresh=False 只读本地缓存）。"""
        credential = store.load_credentials()
        if not credential:
            return {"logged_in": False}
        key = store.account_key(credential)
        meta = store.account_info_for(key)
        info: dict[str, Any] = {
            "logged_in": True,
            "current_key": key,
            "musicid": security.mask(str(credential.get("musicid") or credential.get("str_musicid") or "")),
            "nickname": meta.get("nickname") or "",
            "avatar": meta.get("avatar") or "",
            "vip_level": meta.get("vip_level") or "",
            "vip_desc": meta.get("vip_desc") or "",
            "vip_expire": meta.get("vip_expire") or "",
            "vip_days_left": meta.get("vip_days_left") or 0,
            "login_type": credential.get("login_type") or "",
            "updated_at": credential.get("updated_at") or 0,
        }
        if not refresh:
            return info
        euin = self._euin()
        nickname = info["nickname"]
        avatar = info["avatar"]
        if euin:
            try:
                homepage = await self.call(lambda c: c.user.get_homepage(euin))
                base = self._field(homepage, "base_info", default=None)
                nickname = str(self._field(base, "name", default="") or nickname)
                avatar = str(self._field(base, "avatar", default="") or avatar)
            except errors.LoginExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001 展示信息失败不影响使用
                logger.info("获取账号主页信息失败：%s", security.sanitize_log(str(exc)))
        try:
            vip = await self.call(lambda c: c.user.get_vip_info())
            vip_info = self._vip_summary(vip)
        except errors.LoginExpiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("获取会员信息失败：%s", security.sanitize_log(str(exc)))
            vip_info = {}
        info.update({k: v for k, v in vip_info.items() if v not in (None, "")})
        info["nickname"] = nickname
        info["avatar"] = avatar
        store.update_account_meta(
            key,
            nickname=nickname,
            avatar=avatar,
            vip_level=info.get("vip_level") or "",
            vip_desc=info.get("vip_desc") or "",
            vip_expire=info.get("vip_expire") or "",
            vip_days_left=info.get("vip_days_left") or 0,
        )
        return info

    async def refresh_account_profile(self) -> None:
        """登录成功后顺手缓存昵称/头像/会员信息（失败忽略）。"""
        try:
            await self.account_info(refresh=True)
        except Exception as exc:  # noqa: BLE001
            logger.info("缓存账号信息失败：%s", security.sanitize_log(str(exc)))

    async def create_qrcode(self, login_type: str) -> dict[str, Any]:
        qr_type = _QR_TYPES.get((login_type or "").lower())
        if qr_type is None:
            raise errors.BadRequestError("不支持的登录方式")
        client = await self.client()
        qr = await client.login.get_qrcode(qr_type)
        session_id = uuid.uuid4().hex
        self._prune_sessions()
        self._sessions[session_id] = qr
        self._sessions_created[session_id] = time.time()
        return {
            "session_id": session_id,
            "image": f"data:{qr.mimetype};base64,{base64.b64encode(qr.data).decode()}",
            "qr_type": login_type,
            "expires_in": _SESSION_TTL,
        }

    async def check_qrcode(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise errors.BadRequestError("二维码已失效，请重新获取")
        client = await self.client()
        result = await client.login.check_qrcode(session)
        state = getattr(result.event, "name", str(result.event)).lower()
        payload: dict[str, Any] = {"state": state, "logged_in": False}
        if result.event == QRCodeLoginEvents.DONE and getattr(result, "credential", None):
            self._persist_credential(result.credential)
            self._sessions.pop(session_id, None)
            self._sessions_created.pop(session_id, None)
            payload["logged_in"] = True
        elif result.event in (QRCodeLoginEvents.TIMEOUT, QRCodeLoginEvents.REFUSE):
            self._sessions.pop(session_id, None)
            self._sessions_created.pop(session_id, None)
        return payload

    async def send_authcode(self, phone: str, country_code: int = 86) -> dict[str, Any]:
        if not phone or not phone.strip():
            raise errors.BadRequestError("请输入手机号")
        client = await self.client()
        result = await client.login.send_authcode(phone.strip(), country_code=country_code)
        sent = result.event == PhoneLoginEvents.SEND
        return {
            "sent": sent,
            "event": getattr(result.event, "name", "UNKNOWN").lower(),
            "info": result.info or ("验证码已发送" if sent else "发送失败，请稍后重试"),
        }

    async def phone_login(self, phone: str, auth_code: str) -> dict[str, Any]:
        if not phone.strip() or not auth_code.strip():
            raise errors.BadRequestError("手机号与验证码不能为空")
        client = await self.client()
        credential = await client.login.phone_authorize(phone.strip(), auth_code.strip())
        if not credential:
            raise errors.AppError("登录失败，请检查验证码", code="login_failed")
        self._persist_credential(credential)
        return {"logged_in": True}

    def _prune_sessions(self) -> None:
        now = time.time()
        for session_id in list(self._sessions_created):
            if now - self._sessions_created[session_id] > _SESSION_TTL:
                self._sessions.pop(session_id, None)
                self._sessions_created.pop(session_id, None)

    # ------------------------------------------------------------------
    # 数据归一化：把 SDK 的模型统一成前端可用的纯 dict
    # ------------------------------------------------------------------
    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _field(cls, obj: Any, *keys: str, default: Any = "") -> Any:
        for key in keys:
            if obj is None:
                return default
            value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if value not in (None, "", []):
                return value
        return default

    @classmethod
    def _dump(cls, obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:  # noqa: BLE001
                return {}
        return obj if isinstance(obj, (dict, list)) else {}

    @classmethod
    def _find_list(cls, obj: Any, keys: tuple[str, ...], depth: int = 0) -> list[Any]:
        """按候选字段名深度查找列表，避免依赖单一字段命名。"""
        if obj is None or depth > 4:
            return []
        for key in keys:
            value = cls._field(obj, key, default=None)
            if isinstance(value, list) and value:
                return value
        dumped = cls._dump(obj)
        if isinstance(dumped, dict):
            for value in dumped.values():
                found = cls._find_list(value, keys, depth + 1)
                if found:
                    return found
        elif isinstance(dumped, list):
            for value in dumped:
                found = cls._find_list(value, keys, depth + 1)
                if found:
                    return found
        return []

    def _singer_text(self, song: Any) -> str:
        singers = self._field(song, "singer", "singers", default=[])
        if isinstance(singers, str):
            return singers
        names = []
        for item in singers or []:
            name = self._field(item, "name", "title")
            if name:
                names.append(str(name))
        return "、".join(names)

    def song_summary(self, song: Any) -> dict[str, Any]:
        """把 Song / SongSearch 归一化成前端结构。"""
        album = self._field(song, "album", default=None)
        file_info = self._field(song, "file", default=None)
        sizes: dict[str, int] = {}
        for key in (
            "size_flac", "size_320mp3", "size_128mp3", "size_192ogg",
            "size_192aac", "size_96aac", "size_48aac", "size_24aac",
            "size_dolby", "size_dts",
        ):
            value = self._to_int(self._field(file_info, key, default=0))
            if value:
                sizes[key.replace("size_", "")] = value
        pay = self.pay_info(song)
        return {
            "songmid": str(self._field(song, "mid", "songmid")),
            "songid": self._to_int(self._field(song, "id", "songid", default=0)),
            "name": str(self._field(song, "name", "title")),
            "subtitle": str(self._field(song, "subtitle")),
            "singer": self._singer_text(song),
            "album": str(self._field(album, "name", "title")),
            "album_pmid": str(self._field(album, "pmid")),
            "interval": self._to_int(self._field(song, "interval", default=0)),
            "media_mid": str(self._field(file_info, "media_mid")),
            "song_type": self._to_int(self._field(song, "type", default=1), 1),
            "sizes": sizes,
            # 列表里就能判断"这首能不能下"：QQ 对未购买的付费数字专辑/已下架曲目
            # 会把各音质 size 全部置 0（详情接口也只回空壳），前端据此置灰或隐藏。
            "available": bool(sizes),
            "pay": pay,
        }

    @classmethod
    def _named_text(cls, items: Any, sep: str = "、") -> str:
        """把 info.genre / info.lan 这类 [{id,value}] 结构拼成文本。"""
        if isinstance(items, str):
            return items
        if not isinstance(items, (list, tuple)):
            items = [items]
        names: list[str] = []
        for item in items or []:
            text = str(cls._field(item, "value", "name", "title") or "").strip()
            if text and text not in names:
                names.append(text)
        return sep.join(names)

    @classmethod
    def _id_of(cls, items: Any) -> int:
        """取 info.genre / info.lan 首个元素的数字 ID。"""
        for item in items or []:
            value = cls._field(item, "id", default=None)
            if value not in (None, ""):
                return cls._to_int(value)
        return 0

    @classmethod
    def pay_info(cls, track: Any) -> dict[str, Any]:
        """播放 / 下载 / 付费标记：前端据此提示“付费内容”，下载失败时用于判定失败原因。"""
        pay = cls._field(track, "pay", default=None)
        if pay is None:
            return {}
        return {
            "pay_play": cls._to_int(cls._field(pay, "pay_play", default=0)),
            "pay_down": cls._to_int(cls._field(pay, "pay_down", default=0)),
            "pay_status": cls._to_int(cls._field(pay, "pay_status", default=0)),
            "pay_month": cls._to_int(cls._field(pay, "pay_month", default=0)),
            "price_track": cls._to_int(cls._field(pay, "price_track", default=0)),
            "price_album": cls._to_int(cls._field(pay, "price_album", default=0)),
            "time_free": cls._to_int(cls._field(pay, "time_free", default=0)),
        }

    def song_detail_meta(self, response: Any, track: Any) -> dict[str, Any]:
        """歌曲详情中除基础信息外的发行 / 语言 / 曲目 / 公司等字段（全部来自该歌曲 ID）。"""
        album = self._field(track, "album", default=None)
        extras = self._field(response, "extras", default=None)
        mv = self._field(track, "mv", default=None)
        info = self._field(response, "info", default=None)
        # 详情接口把 流派/语言/发行时间/公司/简介 放在响应根（或 info）里，取值两者都试
        genre_items = self._field(response, "genre", default=None) or self._field(info, "genre", default=None)
        lan_items = (
            self._field(response, "lan", "language", default=None)
            or self._field(info, "lan", "language", default=None)
        )
        company_item = self._field(response, "company", default=None) or self._field(info, "company", default=None)
        company_item = company_item[0] if isinstance(company_item, list) and company_item else company_item
        pub_time = self._named_text(self._field(response, "pub_time", "pubtime", default=None) or self._field(info, "pub_time", default=None), sep=" ")
        intro = self._named_text(self._field(response, "intro", default=None) or self._field(info, "intro", default=None), sep="\n")
        singers = self._field(track, "singer", "singers", default=[]) or []

        cover_url = ""
        getter = getattr(album, "cover_url", None)
        if callable(getter):
            try:
                cover_url = str(getter(500) or "")
            except Exception:  # noqa: BLE001
                cover_url = ""

        date = str(self._field(track, "time_public", default="")) or pub_time
        album_date = str(self._field(album, "time_public", default=""))
        vf = self._field(track, "vf", default=None) or []
        replay: dict[str, Any] = {}
        try:
            numbers = [float(x) for x in vf]
        except (TypeError, ValueError):
            numbers = []
        if len(numbers) >= 3 and (numbers[0] or numbers[1]):
            replay = {"gain": round(numbers[0], 2), "peak": round(numbers[1], 4), "range": round(numbers[2], 2)}

        mv_vid = str(self._field(mv, "vid", "mvid", default=""))
        mv_id = self._to_int(self._field(mv, "id", default=0))
        return {
            "title": str(self._field(track, "title", "name", default="")),
            "subtitle": str(self._field(track, "subtitle", default="")),
            "singers": [
                {"mid": str(self._field(item, "mid", default="")), "name": str(self._field(item, "name", "title", default=""))}
                for item in singers
            ],
            "album_id": self._to_int(self._field(album, "id", default=0)),
            "album_mid": str(self._field(album, "mid", default="")),
            "album_subtitle": str(self._field(album, "subtitle", default="")),
            "album_date": album_date,
            "album_cover_url": cover_url,
            "track_no": self._to_int(self._field(track, "index_album", default=0)),
            "disc_no": self._to_int(self._field(track, "index_cd", default=0)),
            "date": date,
            "year": self._to_int(date[:4]) if date[:4].isdecimal() else 0,
            "genre": self._named_text(genre_items) or str(self._field(track, "genre", default="")),
            "genre_id": self._id_of(genre_items),
            "language": self._named_text(lan_items) or str(self._field(track, "language", default="")),
            "language_id": self._id_of(lan_items),
            "company": self._named_text(company_item, sep="、"),
            "company_id": self._to_int(self._field(company_item, "id", default=0)),
            "bpm": self._to_int(self._field(track, "bpm", default=0)),
            "mv_id": mv_id,
            "mv_vid": mv_vid,
            "trans_name": str(self._field(extras, "transname", "trans_name", default="")),
            "intro": intro or str(self._field(extras, "intro", default="")),
            "from": str(self._field(extras, "from", default="")),
            "wiki_url": str(self._field(extras, "wikiurl", "wiki_url", default="")),
            "replaygain": replay,
            "pay": self.pay_info(track),
        }

    def songlist_summary(self, item: Any) -> dict[str, Any]:
        return {
            "id": self._to_int(self._field(item, "id", "dissid", "tid", default=0)),
            "title": clean_text(self._field(item, "title", "name", "dissname", "dirName")),
            "picurl": str(self._field(item, "picurl", "cover", "logo", "pic")),
            "songnum": self._to_int(self._field(item, "songnum", "songNum", "song_cnt", default=0)),
            "listennum": self._to_int(self._field(item, "listennum", "listen_num", default=0)),
            "creator": clean_text(self._field(item, "nickname", "creator", "username")),
        }

    def singer_summary(self, item: Any) -> dict[str, Any]:
        return {
            "singer_mid": str(self._field(item, "mid", "singer_mid")),
            "name": clean_text(self._field(item, "name", "singer_name", "title")),
            "pmid": str(self._field(item, "pmid")),
        }

    # ------------------------------------------------------------------
    # 业务接口
    # ------------------------------------------------------------------
    async def song_detail(self, songmid: str) -> dict[str, Any]:
        if not songmid:
            raise errors.BadRequestError("缺少歌曲 mid")
        response = await self.call(lambda c: c.song.get_detail(songmid))
        track = self._field(response, "track", default=None)
        if track is None:
            return {}
        detail = self.song_summary(track)
        detail.update(self.song_detail_meta(response, track))
        if not detail.get("album_pmid"):
            detail["album_pmid"] = str(self._field(self._field(track, "album", default=None), "pmid", default=""))
        return detail

    # 制作人 / 演奏角色：接口分组的 Title 优先，其次用已实测确认的 Type 兜底
    PRODUCER_ROLE_NAMES: dict[int, str] = {
        5: "作词",
        6: "作曲",
        7: "制作人",
        8: "编曲",
        9: "混音",
        10: "录音",
        13: "吉他",
        14: "贝斯",
        16: "鼓",
        10000: "演唱",
    }

    def producer_credits(self, response: Any) -> list[dict[str, Any]]:
        """归一化制作人名单：[{role, type, names: [...]}]。"""
        credits: list[dict[str, Any]] = []
        for group in self._field(response, "data", "Lst", default=[]) or []:
            role_type = self._to_int(self._field(group, "type", "Type", default=0))
            role = clean_text(self._field(group, "title", "Title", default="")) or self.PRODUCER_ROLE_NAMES.get(
                role_type, ""
            )
            names: list[str] = []
            for item in self._field(group, "producers", "Producers", default=[]) or []:
                name = clean_text(self._field(item, "name", "Name", default=""))
                if name and name not in names:
                    names.append(name)
            if role and names:
                credits.append({"role": role, "type": role_type, "names": names})
        return credits

    def song_labels(self, response: Any) -> list[str]:
        """归一化榜单 / 标签文案。"""
        texts: list[str] = []
        for item in self._field(response, "labels", "Labels", default=[]) or []:
            text = clean_text(self._field(item, "tag_txt", "tagTxt", "text", default=""))
            if text and text not in texts:
                texts.append(text)
        return texts

    @classmethod
    def _fav_numbers(cls, response: Any, songid: int) -> tuple[str, int]:
        """从收藏响应里取出该歌曲的展示文案与原始值。"""
        show = getattr(response, "show", None) or {}
        numbers = getattr(response, "numbers", None) or {}
        if not isinstance(show, dict):
            show = cls._dump(show)
        if not isinstance(numbers, dict):
            numbers = cls._dump(numbers)
        key = str(songid)
        text = str(show.get(key) or (next(iter(show.values())) if show else "") or "")
        raw = numbers.get(key) or (next(iter(numbers.values())) if numbers else 0)
        return text, cls._to_int(raw)

    async def song_extras(self, songmid: str = "", songid: int = 0) -> dict[str, Any]:
        """歌曲附加元数据：制作人名单 / 榜单标签 / 收藏热度。

        三个接口相互独立，任一失败只影响自身字段，不阻断下载流程。
        """
        result: dict[str, Any] = {"credits": [], "tags": [], "fav_show": "", "fav_count": 0}
        jobs: list[tuple[str, Any]] = []
        if songid:
            jobs.append(("credits", self.call(lambda c: c.song.get_producer(songid))))
            jobs.append(("labels", self.call(lambda c: c.song.get_labels(songid))))
            jobs.append(("fav", self.call(lambda c: c.song.get_fav_num([songid]))))
        elif songmid:
            jobs.append(("credits", self.call(lambda c: c.song.get_producer(songmid))))
        if not jobs:
            return result
        outcomes = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        for (key, _), outcome in zip(jobs, outcomes):
            if isinstance(outcome, BaseException):
                logger.info("附加元数据 %s 获取失败：%s", key, security.sanitize_log(str(outcome)))
                continue
            try:
                if key == "credits":
                    result["credits"] = self.producer_credits(outcome)
                elif key == "labels":
                    result["tags"] = self.song_labels(outcome)
                else:
                    result["fav_show"], result["fav_count"] = self._fav_numbers(outcome, songid)
            except Exception as exc:  # noqa: BLE001
                logger.info("附加元数据 %s 解析失败：%s", key, security.sanitize_log(str(exc)))
        return result

    async def song_lyric(self, value: int | str, trans: bool = True) -> dict[str, str]:
        response = await self.call(lambda c: c.lyric.get_lyric(value, trans=trans))
        return {
            "lyric": str(self._field(response, "lyric", default="")),
            "translation": str(self._field(response, "trans", "translation", default="")),
            "roma": str(self._field(response, "roma", default="")),
        }

    async def search(self, keyword: str, kind: str = "song", page: int = 1, num: int = 20) -> list[dict[str, Any]]:
        type_map = {
            "song": SearchType.SONG,
            "songlist": SearchType.SONGLIST,
            "singer": SearchType.SINGER,
        }
        search_type = type_map.get((kind or "song").lower(), SearchType.SONG)
        response = await self.call(
            lambda c: c.search.search_by_type(keyword, search_type, num=num, page=page)
        )
        if search_type == SearchType.SONGLIST:
            return [self.songlist_summary(i) for i in self._find_list(response, ("songlist", "songlists"))]
        if search_type == SearchType.SINGER:
            return [self.singer_summary(i) for i in self._find_list(response, ("singer", "singers"))]
        return [self.song_summary(i) for i in self._find_list(response, ("song", "songs"))]

    async def songlist_detail(self, songlist_id: int, page: int = 1, num: int = 30) -> dict[str, Any]:
        response = await self.call(
            lambda c: c.songlist.get_detail(songlist_id, num=num, page=page)
        )
        songs = self._find_list(response, ("song", "songs", "songlist", "tracks"))
        info = {
            "id": songlist_id,
            "title": str(self._field(response, "dissname", "dirName", "title", "name")),
            "picurl": str(self._field(response, "logo", "picurl", "cover")),
            "songnum": self._to_int(self._field(response, "songnum", "song_num", "total")),
            "creator": str(self._field(response, "nickname", "creator")),
        }
        return {"info": info, "songs": [self.song_summary(s) for s in songs]}

    async def recommend_songlists(
        self, page: int = 1, num: int = 12, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """推荐歌单：``num`` 为单页条数；给了 ``limit`` 就翻页凑到这个数量（服务器给多少算多少）。"""
        if not limit:
            response = await self.call(lambda c: c.recommend.get_recommend_songlist(page=page, num=num))
            raw = self._find_list(response, ("songlist", "songlists", "disslist", "list", "items"))
            return [self.songlist_summary(i) for i in raw]
        target = max(1, min(60, int(limit)))
        result: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        first_page = max(1, int(page or 1))
        for page_no in range(first_page, first_page + 6):   # 单页最多 30 个，翻 6 页足够覆盖上限
            if len(result) >= target:
                break
            want = max(1, min(30, target - len(result)))
            response = await self.call(
                lambda c, p=page_no, n=want: c.recommend.get_recommend_songlist(page=p, num=n)
            )
            raw = self._find_list(response, ("songlist", "songlists", "disslist", "list", "items"))
            before = len(result)
            for item in raw or []:
                summary = self.songlist_summary(item)
                key = int(summary.get("id") or 0)
                if not key or key in seen_ids:
                    continue
                seen_ids.add(key)
                result.append(summary)
            if len(result) == before:   # 上游开始重复 → 再翻也不会更多
                break
        return result[:target]

    async def recommend_newsongs(self, type_: int = 5, limit: int | None = None) -> list[dict[str, Any]]:
        """新歌推荐：上游一次性返回，``limit`` 只做截断（服务器给得少就少显示）。"""
        response = await self.call(lambda c: c.recommend.get_recommend_newsong(type=type_))
        raw = self._find_list(response, ("song", "songs", "songlist", "list", "items"))
        items = [self.song_summary(s) for s in raw]
        if limit:
            items = items[: max(1, min(100, int(limit)))]
        return items

    def _recommend_append(self, items: list[dict[str, Any]], seen: set[str], raw: Any) -> None:
        """把一批上游歌曲归一化后追加（按 songmid 去重）。"""
        for song in self._find_list(raw, ("song", "songs", "songlist", "list", "items")) or []:
            summary = self.song_summary(song)
            songmid = str(summary.get("songmid") or "")
            if not songmid or songmid in seen:
                continue
            seen.add(songmid)
            items.append(summary)

    async def recommend_guess(self, limit: int = 15, rounds: int = 0) -> list[dict[str, Any]]:
        """猜你喜欢：服务器按当前账号推送。

        上游单次固定只给 5 首（``num`` 由服务端写死），但连续调用会持续给新歌，
        因此按目标数量多轮拉取并按 songmid 去重；``rounds`` 传 0 时按数量自动算轮数（最多 12 轮）。
        """
        target = max(1, min(60, int(limit or 15)))
        auto_rounds = (target + 4) // 5
        max_rounds = max(1, min(12, int(rounds) if rounds else auto_rounds))
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _ in range(max_rounds):
            try:
                response = await self.call(lambda c: c.recommend.get_guess_recommend())
            except Exception:  # noqa: BLE001 —— 已有结果时单轮失败不推翻整体
                if result:
                    break
                raise
            before = len(result)
            self._recommend_append(result, seen, response)
            if len(result) >= target:
                break
            if len(result) == before:
                break   # 上游开始重复 → 再拉也不会更多
        return result[:target]

    async def recommend_radar(self, limit: int = 30, pages: int = 0) -> list[dict[str, Any]]:
        """私人雷达（QQ音乐每日推荐的个人电台）：每页 10 首，按需翻页去重。

        ``pages`` 传 0 时按目标数量自动算页数（最多 10 页）。
        """
        target = max(1, min(100, int(limit or 30)))
        auto_pages = (target + 9) // 10
        max_pages = max(1, min(10, int(pages) if pages else auto_pages))
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            try:
                response = await self.call(
                    lambda c, p=page: c.recommend.get_radar_recommend(page=p)
                )
            except Exception:  # noqa: BLE001
                if result:
                    break
                raise
            before = len(result)
            self._recommend_append(result, seen, response)
            if len(result) >= target or len(result) == before:
                break
            if not self._field(response, "has_more", default=False):
                break
        return result[:target]

    def _euin(self) -> str:
        credential = self._credential_object()
        if credential is None:
            raise errors.NotLoggedInError()
        euin = str(self._field(credential, "str_musicid", "musicid", default="") or "")
        if not euin:
            raise errors.NotLoggedInError()
        return euin

    async def favourite_songs(self, page: int = 1, num: int = 30) -> list[dict[str, Any]]:
        euin = self._euin()
        response = await self.call(lambda c: c.user.get_fav_song(euin, page=page, num=num))
        raw = self._find_list(response, ("song", "songs", "songlist", "list", "items"))
        return [self.song_summary(s) for s in raw]

    async def favourite_songlists(self) -> list[dict[str, Any]]:
        """我的歌单：自建歌单（含「我喜欢」）在前，收藏的外部歌单在后。

        上游收藏歌单接口（PlaylistFavRead/CgiGetPlaylistFavInfo）会直接返回
        code=80050，这里降级为只返回自建歌单，避免整个「我的歌单」页面 500。
        """
        created_uin = self._to_int(self._euin(), 0)
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        failures: list[BaseException] = []

        def collect(response: Any) -> None:
            raw = self._find_list(
                response,
                ("playlist", "playlists", "songlist", "songlists", "disslist", "list", "items"),
            )
            for entry in raw or []:
                summary = self.songlist_summary(entry)
                if summary["id"] and summary["id"] not in seen:
                    seen.add(summary["id"])
                    items.append(summary)

        try:
            collect(await self.call(lambda c: c.user.get_created_songlist(created_uin)))
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
            logger.info("获取自建歌单失败：%s", security.sanitize_log(str(exc)))

        try:
            collect(await self.call(lambda c: c.user.get_fav_songlist(self._euin())))
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)
            logger.info("获取收藏歌单失败：%s", security.sanitize_log(str(exc)))

        if not items and failures:
            raise failures[0]
        return items

    # ------------------------------------------------------------------
    # 播放/下载直链（purl 需与 CDN 节点拼接）
    # ------------------------------------------------------------------
    async def cdn_host(self) -> str:
        response = await self.call(lambda c: c.song.get_cdn_dispatch())
        sips = [str(s) for s in (self._field(response, "sip", default=[]) or []) if s]
        if not sips:
            raise errors.AppError("无法获取 CDN 节点，请稍后重试", code="cdn_unavailable")
        return random.choice(sips)

    @staticmethod
    def resolve_file_type(quality: str) -> Any:
        kind, type_name = env.QUALITY_MAP.get(quality, env.QUALITY_MAP[env.DEFAULT_QUALITY])
        enum_cls = EncryptedSongFileType if kind == "encrypted" else SongFileType
        return getattr(enum_cls, type_name)

    async def song_urls(self, infos: list[SongFileInfo], file_type: Any) -> Any:
        return await self.call(lambda c: c.song.get_song_urls(file_info=infos, file_type=file_type))

    async def song_url(self, songmid: str, quality: str = "") -> dict[str, Any]:
        """取指定音质的直链（预览与下载共用，不创建任务）。"""
        quality = quality or env.DEFAULT_QUALITY
        file_type = self.resolve_file_type(quality)
        detail = await self.song_detail(songmid)
        info = SongFileInfo(
            mid=songmid,
            file_type=file_type,
            song_type=self._to_int(detail.get("song_type", 1), 1),
            media_mid=detail.get("media_mid") or None,
        )
        response = await self.song_urls([info], file_type)
        items = getattr(response, "data", None) or []
        item = next(
            (i for i in items if str(getattr(i, "mid", "")) == songmid),
            items[0] if items else None,
        )
        if item is None:
            raise errors.AppError("未获取到音频地址", code="no_url")
        purl = str(getattr(item, "purl", "") or "")
        result = self._to_int(getattr(item, "result", -1), -1)
        if result != 0 or not purl:
            raise errors.AppError("该音质不可用（可能受版权或会员限制）", code="no_permission")
        host = await self.cdn_host()
        filename = str(getattr(item, "filename", "") or "")
        ekey = str(getattr(item, "ekey", "") or "")
        kind, _ = env.QUALITY_MAP.get(quality, ("plain", ""))
        name_part = filename.rsplit("/", 1)[-1]
        ext = ("." + name_part.rsplit(".", 1)[1].lower()) if "." in name_part else ""
        return {
            "url": host + purl,
            "filename": filename,
            "quality": quality,
            "quality_label": env.quality_label(quality),
            "encrypted": kind == "encrypted",
            "ekey": ekey,
            "ext": ext,
            "expires_in": self._to_int(getattr(response, "expiration", 0)),
        }

    async def preview_url(self, songmid: str, quality: str = "") -> dict[str, Any]:
        return await self.song_url(songmid, quality)


service = QQService()
