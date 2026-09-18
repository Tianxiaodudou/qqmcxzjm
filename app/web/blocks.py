"""首页板块数据：QQ音乐首页信息流 / 榜单 / 新碟 / 歌手 / MV / 热搜 / 每日30首 / 相似歌曲 / 我的收藏.

这一层只负责「取数 + 归一化成前端好用的结构」，全部方法混入 QQService，
沿用 service.py 里的字段兜底工具（_field / _to_int / song_summary ...）。
公共（匿名）数据带 5 分钟内存缓存：既让页面秒开，也顺手压住 QQ 侧的限流。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("qqmusic.blocks")

# 公共板块数据的缓存时长（秒）：这些接口内容按天/小时更新，5 分钟足够新鲜
CACHE_TTL = 300.0
_CACHE: dict[tuple[Any, ...], tuple[float, Any]] = {}
_CACHE_MAX = 240

# QQ音乐信息流卡片里，只有这两类能直接落到我们已有的详情页
CARD_SONG = 200
CARD_SONGLIST = 500


def mv_url_playable(url: str) -> bool:
    """判断 MV 直链是否真能播。

    QQ音乐在未登录 / 无版权时会回一组「只有域名、没有路径」的占位地址
    （如 ``http://mv6.music.tc.qq.com/``）。这种地址塞进 <video> 只会黑屏，
    所以只认真正带路径（通常还带 vkey）的地址——过滤后前端能给出「先登录」的提示。
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.path.strip("/") or parts.query)


def cache_get(key: tuple[Any, ...]) -> Any:
    """读缓存；过期即丢。"""
    hit = _CACHE.get(key)
    if not hit:
        return None
    stamp, value = hit
    if time.time() - stamp > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def cache_put(key: tuple[Any, ...], value: Any) -> None:
    """写缓存（超过上限先按时间淘汰一批，避免长期跑内存涨）。"""
    _CACHE[key] = (time.time(), value)
    if len(_CACHE) > _CACHE_MAX:
        for old_key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 3]:
            _CACHE.pop(old_key, None)


def cache_clear() -> None:
    """清空缓存（设置里的数量上限变化后调用，让新上限立刻生效）。"""
    _CACHE.clear()


class BlocksMixin:
    """首页新板块的取数逻辑。"""

    # ------------------------------------------------------------------
    # 归一化：卡片类
    # ------------------------------------------------------------------
    def album_card(self, item: Any) -> dict[str, Any]:
        return {
            "album_mid": str(self._field(item, "mid", "album_mid")),
            "album_id": self._to_int(self._field(item, "id", "album_id", default=0)),
            "name": str(self._field(item, "name", "title", "album_name")),
            "cover": str(self._field(item, "cover", "picurl", "pic")),
            "singer": self._named_singers(self._field(item, "singers", "singer", default=[])),
            "singer_mid": self._first_singer_mid(self._field(item, "singers", "singer", default=[])),
            "release_time": str(self._field(item, "release_time", "time_public", default="")),
            "total": self._to_int(self._field(item, "total_num", "songnum", "total", default=0)),
        }

    def mv_card(self, item: Any) -> dict[str, Any]:
        return {
            "vid": str(self._field(item, "vid", "mv_vid")),
            "mv_id": self._to_int(self._field(item, "id", "mv_id", default=0)),
            "name": str(self._field(item, "name", "title")),
            "cover": str(self._field(item, "picurl", "cover", "pic")),
            "singer": self._named_singers(self._field(item, "singers", "singer", default=[])),
            "singer_mid": self._first_singer_mid(self._field(item, "singers", "singer", default=[])),
            "duration": self._to_int(self._field(item, "duration", default=0)),
            "playcnt": self._to_int(self._field(item, "playcnt", "play_cnt", default=0)),
        }

    def _named_singers(self, items: Any) -> str:
        if isinstance(items, str):
            return items
        names: list[str] = []
        for item in items or []:
            name = str(self._field(item, "name", "title") or "").strip()
            if name and name not in names:
                names.append(name)
        return "、".join(names)

    def _first_singer_mid(self, items: Any) -> str:
        for item in items or []:
            mid = str(self._field(item, "mid", "singer_mid") or "")
            if mid:
                return mid
        return ""

    # ------------------------------------------------------------------
    # 1. 首页信息流（QQ音乐 App 首页那些卡片）
    # ------------------------------------------------------------------
    async def home_feed(self, page: int = 1, limit: int = 30, resolve: bool = False) -> dict[str, Any]:
        """首页信息流：按 shelf（板块）分组返回；只保留能落到详情页的卡片。

        原生结构是 shelves → niches → cards 三层，卡片类型上百种（游戏、电台、
        直播…），这里只挑两类有意义的：歌曲卡（能试听/下载）与歌单卡（能打开歌单）。

        resolve=True 时额外把歌曲卡（只带数字 ID）换成完整歌曲摘要放进 songs，
        前端才能像别的板块那样勾选下载——代价是每首歌多一次详情请求（并发 4 路）。
        """
        key = ("feed", page, limit, bool(resolve))
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.recommend.get_home_feed(page))
        groups: list[dict[str, Any]] = []
        total = 0
        for shelf in self._field(response, "shelves", default=[]) or []:
            title = str(self._field(shelf, "title_content", "title_template", default="") or "").strip()
            if not title or title.startswith("{"):
                title = "为你推荐"
            cards: list[dict[str, Any]] = []
            for niche in self._field(shelf, "niches", default=[]) or []:
                for raw in self._field(niche, "cards", default=[]) or []:
                    card = self._feed_card(raw)
                    if card:
                        cards.append(card)
            if not cards:
                continue
            groups.append({"title": title, "cards": cards[:limit], "songs": []})
            total += len(cards[:limit])
            if total >= limit * 2:
                break
        if resolve and groups:
            await self._resolve_feed_songs(groups, limit)
        data = {"groups": groups, "total": total}
        cache_put(key, data)
        return data

    async def _resolve_feed_songs(self, groups: list[dict[str, Any]], limit: int) -> None:
        """把信息流的分组里「歌曲卡」补成完整歌曲摘要（写入 group["songs"]）。

        卡片本身只剩数字 ID，这里并发 4 路调详情补齐 songmid/音质/时长；单张失败就跳过，
        不因为一首歌拿不到而让整块信息流失效。
        """
        import asyncio

        cards = [c for group in groups for c in group["cards"] if c.get("kind") == "song"][:limit]
        if not cards:
            return
        sem = asyncio.Semaphore(4)

        async def one(card: dict[str, Any]) -> dict[str, Any] | None:
            async with sem:
                try:
                    summary = await self.song_detail_by_id(int(card["songid"]))
                except Exception:  # noqa: BLE001 —— 单卡失败不影响整块
                    return None
            return summary if summary.get("songmid") else None

        resolved = await asyncio.gather(*(one(card) for card in cards))
        by_id = {int(card["songid"]): song for card, song in zip(cards, resolved) if song}
        for group in groups:
            group["songs"] = [
                by_id[int(card["songid"])]
                for card in group["cards"]
                if card.get("kind") == "song" and int(card["songid"]) in by_id
            ]

    def _feed_card(self, raw: Any) -> dict[str, Any] | None:
        """信息流单卡归一化；不认识的卡片返回 None（直接丢弃）。"""
        if isinstance(raw, dict):
            get = raw.get
        else:  # pragma: no cover —— 模型化对象兜底
            def get(name: str, default: Any = None) -> Any:
                return getattr(raw, name, default)
        ctype = self._to_int(get("type"))
        extra = get("extra_info") or {}
        module = str(extra.get("moduleID") or "") if isinstance(extra, dict) else ""
        title = str(get("title") or "").strip()
        subtitle = str(get("subtitle") or "").strip()
        cover = str(get("cover") or "")
        if ctype == CARD_SONG and not module.startswith("playlist"):
            song_id = self._to_int(get("id"))
            if not song_id or not title:
                return None
            return {
                "kind": "song",
                "songid": song_id,
                "name": title,
                "singer": subtitle,
                "cover": cover,
            }
        if module.startswith("playlist"):
            list_id = self._to_int(get("id"))
            if not list_id or not title:
                return None
            return {
                "kind": "songlist",
                "songlist_id": list_id,
                "name": title,
                "singer": subtitle,
                "cover": cover,
            }
        return None

    # ------------------------------------------------------------------
    # 2. 排行榜
    # ------------------------------------------------------------------
    async def top_categories(self) -> list[dict[str, Any]]:
        """榜单分类（巅峰榜/地区榜/特色榜…）；每组带子榜列表。"""
        cached = cache_get(("topcat",))
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.top.get_category())
        groups: list[dict[str, Any]] = []
        for group in self._field(response, "group", default=[]) or []:
            items = []
            for item in self._field(group, "toplist", default=[]) or []:
                items.append(
                    {
                        "top_id": self._to_int(self._field(item, "id", default=0)),
                        "name": str(self._field(item, "name", "title")),
                        "period": str(self._field(item, "period", default="")),
                        "listen_num": self._to_int(self._field(item, "listen_num", default=0)),
                        "total": self._to_int(self._field(item, "total_num", default=0)),
                    }
                )
            if items:
                groups.append({"name": str(self._field(group, "name", default="榜单")), "items": items})
        cache_put(("topcat",), groups)
        return groups

    async def top_songs(self, top_id: int, limit: int = 30) -> dict[str, Any]:
        """某个榜单的歌曲列表（热歌榜=26、飙升榜=62、流行指数榜=4…）。"""
        if top_id <= 0:
            raise ValueError("缺少榜单 ID")
        key = ("top", top_id, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.top.get_detail(top_id, limit))
        info = self._field(response, "info", default=None)
        songs = [self.song_summary(item) for item in (self._field(response, "songs", default=[]) or [])]
        data = {
            "top_id": top_id,
            "name": str(self._field(info, "name", "title", default="榜单")),
            "period": str(self._field(info, "period", default="")),
            "total": self._to_int(self._field(info, "total_num", default=0)),
            "songs": songs[:limit],
        }
        cache_put(key, data)
        return data

    # ------------------------------------------------------------------
    # 3. 新碟上架 / 专辑详情
    # ------------------------------------------------------------------
    async def new_albums(self, area: int = 1, limit: int = 30, page: int = 1) -> dict[str, Any]:
        key = ("newalbum", area, limit, page)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.album.get_new_album(area=area, num=limit, page=page))
        albums = [self.album_card(item) for item in (self._field(response, "albums", default=[]) or [])]
        data = {
            "area": area,
            "total": self._to_int(self._field(response, "total", default=0)),
            "albums": albums[:limit],
        }
        cache_put(key, data)
        return data

    async def album_detail(self, album_mid: str, limit: int = 30) -> dict[str, Any]:
        if not album_mid:
            raise ValueError("缺少专辑 mid")
        key = ("album", album_mid, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        # 专辑信息在 AlbumInfoServer.GetAlbumDetail，歌曲列表在 AlbumSongList.GetAlbumSongList，
        # 两个接口各管一半；并行取回，避免串行多等一个 RTT。
        detail, song_resp = await asyncio.gather(
            self.call(lambda c: c.album.get_detail(album_mid)),
            self.call(lambda c: c.album.get_song(album_mid, limit, 1)),
        )
        album = self._field(detail, "album", "info", default=None)
        card = self.album_card(album) if album is not None else {}
        if not card.get("singer"):
            # 专辑详情把歌手放在顶层 singers 里，而卡片结构统一从 album 里取
            singers = self._field(detail, "singers", "singer", default=[])
            card["singer"] = self._named_singers(singers)
            card["singer_mid"] = card.get("singer_mid") or self._first_singer_mid(singers)
        songs = [
            self.song_summary(item)
            for item in (self._field(song_resp, "song_list", default=[]) or [])
        ]
        data = {"album": card, "songs": songs[:limit]}
        cache_put(key, data)
        return data

    # ------------------------------------------------------------------
    # 4. 歌手
    # ------------------------------------------------------------------
    async def chart_singers(self, area: int = -100, sex: int = -100, genre: int = -100, limit: int = 30) -> list[dict[str, Any]]:
        """歌手分类榜：地区(内地/港台/欧美/日韩)×性别×风格。"""
        key = ("singerlist", area, sex, genre, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.singer.get_singer_list(area=area, sex=sex, genre=genre))
        singers = []
        for item in (self._field(response, "singerlist", default=[]) or [])[:limit]:
            singers.append(
                {
                    "singer_mid": str(self._field(item, "mid", "singer_mid")),
                    "name": str(self._field(item, "name", "singer_name", "title")),
                    "other_name": str(self._field(item, "other_name", default="")),
                    "pmid": str(self._field(item, "pmid", default="")),
                    "concern_num": self._to_int(self._field(item, "concern_num", default=0)),
                }
            )
        cache_put(key, singers)
        return singers

    async def singer_profile(self, singer_mid: str) -> dict[str, Any]:
        """歌手主页头部：资料 + 相似歌手。"""
        if not singer_mid:
            raise ValueError("缺少歌手 mid")
        key = ("singer", singer_mid)
        cached = cache_get(key)
        if cached is not None:
            return cached
        info = await self.call(lambda c: c.singer.get_info(singer_mid))
        base = self._field(info, "base_info", default=None)
        singer = self._field(info, "singer", default=None)
        similar_resp = await self.call(lambda c: c.singer.get_similar(singer_mid, 12))
        data = {
            "singer_mid": singer_mid,
            "name": str(self._field(base, "name", "title", default="") or self._field(singer, "name", default="")),
            "avatar": str(self._field(base, "avatar", "background_image", default="")),
            "pmid": str(self._field(singer, "singer_pmid", "pmid", default="")),
            "similar": [
                {
                    "singer_mid": str(self._field(item, "mid", "singer_mid")),
                    "name": str(self._field(item, "name", "title")),
                    "cover": str(self._field(item, "singer_pic", "picurl", "cover", default="")),
                }
                for item in (self._field(similar_resp, "singerlist", default=[]) or [])
            ],
        }
        cache_put(key, data)
        return data

    async def singer_songs(self, singer_mid: str, limit: int = 30) -> list[dict[str, Any]]:
        key = ("singersongs", singer_mid, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.singer.get_songs_list(singer_mid, limit, 1))
        songs = [self.song_summary(item) for item in (self._field(response, "song_list", default=[]) or [])][:limit]
        cache_put(key, songs)
        return songs

    async def singer_albums(self, singer_mid: str, limit: int = 30) -> list[dict[str, Any]]:
        key = ("singeralbums", singer_mid, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.singer.get_album_list(singer_mid, limit, 1))
        albums = [self.album_card(item) for item in (self._field(response, "album_list", default=[]) or [])][:limit]
        cache_put(key, albums)
        return albums

    async def singer_mvs(self, singer_mid: str, limit: int = 30) -> list[dict[str, Any]]:
        key = ("singermvs", singer_mid, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.singer.get_mv_list(singer_mid, limit, 1))
        mvs = [self.mv_card(item) for item in (self._field(response, "mv_list", default=[]) or [])][:limit]
        cache_put(key, mvs)
        return mvs

    # ------------------------------------------------------------------
    # 5. MV
    # ------------------------------------------------------------------
    async def mv_list(self, area: int = 15, version: int = 7, order: int = 0, limit: int = 30, page: int = 1) -> dict[str, Any]:
        key = ("mvlist", area, version, order, limit, page)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(
            lambda c: c.mv.get_mv_list(area=area, version=version, order=order, num=limit, page=page)
        )
        items = [self.mv_card(item) for item in (self._field(response, "items", default=[]) or [])][:limit]
        data = {"total": self._to_int(self._field(response, "total", default=0)), "items": items}
        cache_put(key, data)
        return data

    async def mv_play_url(self, vid: str) -> dict[str, Any]:
        """取 MV 播放直链。

        QQ 侧每档清晰度给了三个字段：``url`` / ``freeflow_url`` / ``comm_url``。
        未登录时 ``url`` 往往只有裸域名占位（``http://mv6.music.tc.qq.com/``），
        真正能播的地址在 ``freeflow_url``（免流量 CDN，自带 vkey）。所以三个字段都收，
        再按 mv_url_playable 过滤掉占位地址。
        """
        if not vid:
            raise ValueError("缺少 MV vid")
        response = await self.call(lambda c: c.mv.get_mv_urls([vid]))
        entry = (self._field(response, "data", default={}) or {}).get(vid) or {}
        urls: list[str] = []
        m3u8 = ""
        for item in self._field(entry, "mp4", default=[]) or []:
            for field in ("url", "freeflow_url", "comm_url"):
                candidates = self._field(item, field, default=[]) or []
                if isinstance(candidates, str):
                    candidates = [candidates]
                for url in candidates:
                    if isinstance(url, str) and url.startswith("http") and mv_url_playable(url):
                        if url not in urls:
                            urls.append(url)
            if not m3u8:
                m3u8 = str(self._field(item, "m3u8", default="") or "")
        return {"vid": vid, "urls": urls, "m3u8": m3u8}

    # ------------------------------------------------------------------
    # 6. 热搜
    # ------------------------------------------------------------------
    async def hot_keys(self, limit: int = 30) -> list[dict[str, Any]]:
        key = ("hotkey", limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.search.get_hotkey())
        keys = []
        for item in (self._field(response, "vec_hotkey", default=[]) or [])[:limit]:
            query = str(self._field(item, "query", "title", "hotkey") or "").strip()
            if not query:
                continue
            keys.append({"query": query, "score": self._to_int(self._field(item, "score", default=0))})
        cache_put(key, keys)
        return keys

    # ------------------------------------------------------------------
    # 7. 每日30首
    #    QQ音乐官方的「每日30首」是登录后按账号生成的私人歌单，匿名拿不到稳定的
    #    disstid；这里默认从公开歌单里挑一个日更的（搜索「每日30首」中播放量最高、
    #    曲目数 ≥30 的那个），设置里也可以直接填自己的歌单 ID。
    # ------------------------------------------------------------------
    async def daily_songs(self, limit: int = 30, songlist_id: int = 0) -> dict[str, Any]:
        key = ("daily", songlist_id, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        target = songlist_id or await self._daily_songlist_id()
        if not target:
            return {"info": {}, "songs": []}
        data = await self.songlist_detail(target, page=1, num=limit)
        cache_put(key, data)
        return data

    async def _daily_songlist_id(self) -> int:
        """挑一个公开的「每日30首」歌单；候选按播放量取最高的。"""
        cached = cache_get(("dailyid",))
        if cached is not None:
            return self._to_int(cached)
        best_id, best_listen = 0, -1
        try:
            items = await self.search("每日30首", "songlist", 1, 30)
        except Exception as exc:  # noqa: BLE001 —— 搜索失败就当作没有可用歌单
            logger.info("解析每日30首歌单失败：%s", exc)
            items = []
        for item in items:
            if "每日30首" not in str(item.get("title") or ""):
                continue
            if self._to_int(item.get("songnum")) < 30:
                continue
            listen = self._to_int(item.get("listennum"))
            if listen > best_listen:
                best_listen, best_id = listen, self._to_int(item.get("id"))
        cache_put(("dailyid",), best_id)
        return best_id

    # ------------------------------------------------------------------
    # 8. 相似歌曲
    # ------------------------------------------------------------------
    async def similar_songs(self, song_id: int = 0, songmid: str = "", limit: int = 30) -> dict[str, Any]:
        """按「种子歌」推相似歌曲；只给 mid 时先换成数字 ID（接口按数字 ID 工作）。"""
        if not song_id and songmid:
            detail = await self.call(lambda c: c.song.get_detail(songmid))
            track = self._field(detail, "track", "song", default=None)
            song_id = self._to_int(self._field(track, "id", default=0))
        if not song_id:
            return {"seed": {}, "songs": [], "tags": []}
        key = ("similar", song_id, limit)
        cached = cache_get(key)
        if cached is not None:
            return cached
        response = await self.call(lambda c: c.song.get_similar_song(song_id))
        # 响应是「分组」结构：[{title_template, title_content, song:[单曲, ...]}, ...]
        # 需要先把各组里的 song 数组摊平，才能拿到真正的单曲条目。
        groups = self._field(response, "song", "songs", default=[]) or []
        if not isinstance(groups, (list, tuple)):
            groups = [groups]
        raw_songs: list[Any] = []
        for group in groups:
            items = self._field(group, "song", "songs", default=None)
            if items is None:
                raw_songs.append(group)  # 已经是单曲结构，直接收下
            elif isinstance(items, (list, tuple)):
                raw_songs.extend(items)
            else:
                raw_songs.append(items)
        songs = [
            summary
            for summary in (self.song_summary(item) for item in raw_songs)
            if summary.get("songmid")
        ][:limit]
        tags = [str(self._field(item, "tag", default="")) for item in (self._field(response, "tag", default=[]) or [])]
        seed = next((s for s in songs if s.get("songid") == song_id), {})
        data = {"seed": seed, "songs": songs, "tags": [t for t in tags if t][:6]}
        cache_put(key, data)
        return data

    # ------------------------------------------------------------------
    # 9. 歌曲卡片增强：收藏数 / 评论数 / 标签 / 热评
    # ------------------------------------------------------------------
    async def song_stats(self, songmid: str, song_id: int = 0, limit: int = 6) -> dict[str, Any]:
        """歌曲的收藏数、评论数、标签与热评（热评需要登录，取不到就只回前面的）。"""
        if not songmid and not song_id:
            raise ValueError("缺少歌曲 mid")
        detail = await self.call(lambda c: c.song.get_detail(songmid or str(song_id)))
        track = self._field(detail, "track", "song", default=None)
        if track is None:
            return {"labels": [], "fav": 0, "fav_text": "", "comments": 0, "hot": []}
        target_id = self._to_int(self._field(track, "id", default=0)) or song_id
        # 收藏数 / 榜单标签 / 制作人：直接复用已上线的 song_extras（内部已做单项容错）
        extras = await self.song_extras(songid=target_id)
        labels = list(extras.get("tags") or [])
        fav_text = str(extras.get("fav_show") or "")
        fav_count = self._to_int(extras.get("fav_count"))
        comments = 0
        hot: list[dict[str, Any]] = []
        try:
            count_resp = await self.call(lambda c: c.comment.get_comment_count(target_id, 1, 0))
            comments = self._to_int(self._field(count_resp, "comment_count", "count", "total", default=0))
        except Exception as exc:  # noqa: BLE001 —— 评论接口对未登录/部分曲目不开放，不影响其它信息
            logger.info("取评论数失败 songid=%s: %s", target_id, exc)
        try:
            hot_resp = await self.call(lambda c: c.comment.get_hot_comments(target_id, 1, 0, limit))
            for item in self._find_list(hot_resp, ("comments", "comment_list", "hot_comments"), depth=1) or []:
                hot.append(
                    {
                        "nick": str(self._field(item, "nick", "nickname", "user_name", default="")),
                        "text": str(self._field(item, "content", "rootcommentcontent", "text", default="")),
                        "praise": self._to_int(self._field(item, "praise_num", "praisenum", default=0)),
                    }
                )
        except Exception as exc:  # noqa: BLE001 —— 热评需要登录，失败就静默跳过
            logger.info("取热评失败 songid=%s: %s", target_id, exc)
        return {
            "songid": target_id,
            "labels": labels,
            "fav": fav_count,
            "fav_text": fav_text,
            "comments": comments,
            "hot": hot[:limit],
        }

    # ------------------------------------------------------------------
    # 10. 我的收藏（歌曲 / 歌单 / 专辑）
    # ------------------------------------------------------------------
    async def favourite(self, kind: str = "song", page: int = 1, num: int = 30) -> dict[str, Any]:
        kind = (kind or "song").lower()
        if kind == "songlist":
            return {"kind": kind, "songlists": await self.favourite_songlists()}
        if kind == "album":
            euin = self._euin()
            response = await self.call(lambda c: c.user.get_fav_album(euin, page, num))
            albums = [self.album_card(item) for item in (self._find_list(response, ("albumlist", "album_list", "list")) or [])]
            return {"kind": kind, "albums": albums[:num]}
        return {"kind": "song", "songs": await self.favourite_songs(page, num)}

    # ------------------------------------------------------------------
    # 11. 信息流歌曲卡 → 歌曲详情（卡片只带数字 ID）
    # ------------------------------------------------------------------
    async def song_detail_by_id(self, song_id: int) -> dict[str, Any]:
        if not song_id:
            raise ValueError("缺少歌曲 ID")
        detail = await self.call(lambda c: c.song.get_detail(str(song_id)))
        track = self._field(detail, "track", "song", default=None)
        if track is None:
            return {}
        result = self.song_summary(track)
        result.update(self.song_detail_meta(detail, track))
        return result

    # ------------------------------------------------------------------
    # 12. 相似歌曲的种子：最近下载过的一首（没有就让前端自己指定）
    # ------------------------------------------------------------------
    def recent_song_seed(self) -> dict[str, Any]:
        from . import store  # 局部导入：避免与 store 的循环依赖

        for item in store.load_history():
            mid = str(item.get("songmid") or "")
            if mid:
                return {"songmid": mid, "name": str(item.get("name") or ""), "singer": str(item.get("singer") or "")}
        return {}
