"""QQMusicApi 服务封装：客户端生命周期、凭证刷新、登录与业务调用。"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from qqmusic_api import Client
from qqmusic_api.models.login import PhoneLoginEvents, QR, QRCodeLoginEvents, QRLoginType
from qqmusic_api.models.request import Credential
from qqmusic_api.modules.search import SearchType
from qqmusic_api.modules.song import EncryptedSongFileType, SongFileInfo, SongFileType

from . import env, errors, security, store

logger = logging.getLogger("qqmusic.service")

T = TypeVar("T")

_QR_TYPES = {"qq": QRLoginType.QQ, "wx": QRLoginType.WX, "mobile": QRLoginType.MOBILE}
_SESSION_TTL = 300


class QQService:
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

    async def logout(self) -> None:
        store.clear_credentials()
        if self._client is not None:
            self._client.credential = None
        self._sessions.clear()
        self._sessions_created.clear()

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
        }

    def songlist_summary(self, item: Any) -> dict[str, Any]:
        return {
            "id": self._to_int(self._field(item, "id", "dissid", "tid", default=0)),
            "title": str(self._field(item, "title", "name", "dissname", "dirName")),
            "picurl": str(self._field(item, "picurl", "cover", "logo", "pic")),
            "songnum": self._to_int(self._field(item, "songnum", "songNum", "song_cnt", default=0)),
            "listennum": self._to_int(self._field(item, "listennum", "listen_num", default=0)),
            "creator": str(self._field(item, "nickname", "creator", "username")),
        }

    def singer_summary(self, item: Any) -> dict[str, Any]:
        return {
            "singer_mid": str(self._field(item, "mid", "singer_mid")),
            "name": str(self._field(item, "name", "singer_name", "title")),
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
        return self.song_summary(track)

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

    async def recommend_songlists(self, page: int = 1, num: int = 12) -> list[dict[str, Any]]:
        response = await self.call(lambda c: c.recommend.get_recommend_songlist(page=page, num=num))
        raw = self._find_list(response, ("songlist", "songlists", "disslist", "list", "items"))
        return [self.songlist_summary(i) for i in raw]

    async def recommend_newsongs(self, type_: int = 5) -> list[dict[str, Any]]:
        response = await self.call(lambda c: c.recommend.get_recommend_newsong(type=type_))
        raw = self._find_list(response, ("song", "songs", "songlist", "list", "items"))
        return [self.song_summary(s) for s in raw]

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
        euin = self._euin()
        response = await self.call(lambda c: c.user.get_fav_songlist(euin))
        raw = self._find_list(response, ("songlist", "songlists", "disslist", "list", "items"))
        return [self.songlist_summary(i) for i in raw]

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
