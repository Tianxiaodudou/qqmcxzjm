"""首页新板块 API：信息流 / 排行榜 / 新碟 / 歌手 / 热搜 / 每日30首 / 相似歌曲 / 我的收藏。

各板块「显示上限」统一从设置页读取（缺省用 env.DEFAULT_HOME_BLOCK_MAX）；
「是否在首页显示」由前端按设置开关决定，接口本身总是可用。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from . import blocks, env, errors, store
from .context import service

logger = logging.getLogger("qqmusic.api.blocks")
router = APIRouter(tags=["blocks"])


def _limit(key: str, fallback: int) -> int:
    """板块显示上限：优先设置页里的值，读不到时退回内置默认。"""
    try:
        value = int(store.load_settings().get(key) or 0)
    except Exception:  # noqa: BLE001 —— 设置损坏不该拖垮首页
        value = 0
    return value or fallback


# --------------------------------------------------------------------------
# 1. 首页信息流
# --------------------------------------------------------------------------
@router.get("/home/feed")
async def home_feed(
    page: int = Query(1, ge=1),
    limit: int | None = Query(None, ge=1, le=100, description="每个板块最多几张卡片（缺省取设置）"),
    force: bool = Query(False, description="忽略缓存重新拉取"),
    songs: bool = Query(False, description="把歌曲卡解析成可下载的完整歌曲（首页信息流用）"),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_feed_max", env.DEFAULT_HOME_BLOCK_MAX)
    if force:
        blocks.cache_clear()
    data = await service.home_feed(page, limit, resolve=songs)
    return {"ok": True, **data}


# --------------------------------------------------------------------------
# 2. 排行榜
# --------------------------------------------------------------------------
@router.get("/chart/categories")
async def chart_categories() -> dict[str, Any]:
    groups = await service.top_categories()
    return {"ok": True, "groups": groups}


@router.get("/chart/songs")
async def chart_songs(
    top_id: int = Query(26, ge=1, description="榜单 ID，26=热歌榜"),
    limit: int | None = Query(None, ge=1, le=100),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_chart_max", env.DEFAULT_HOME_BLOCK_MAX)
    data = await service.top_songs(top_id, limit)
    return {"ok": True, **data}


# --------------------------------------------------------------------------
# 3. 新碟上架 / 专辑详情
# --------------------------------------------------------------------------
@router.get("/album/new")
async def album_new(
    area: int = Query(1, ge=1, le=6, description="地区：1=内地 2=港台 3=欧美 4=韩国 5=日本 6=其它"),
    page: int = Query(1, ge=1),
    limit: int | None = Query(None, ge=1, le=100),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_newalbum_max", env.DEFAULT_HOME_BLOCK_MAX)
    data = await service.new_albums(area, limit, page)
    return {"ok": True, **data}


@router.get("/album/songs")
async def album_songs(
    album_mid: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    data = await service.album_detail(album_mid, limit)
    return {"ok": True, **data}


# --------------------------------------------------------------------------
# 4. 歌手
# --------------------------------------------------------------------------
@router.get("/singer/chart")
async def singer_chart(
    area: int = Query(-100, description="地区：-100=全部 1=内地 2=港台 3=欧美 4=日本 5=韩国"),
    sex: int = Query(-100, description="性别：-100=全部 0=男 1=女 2=组合"),
    genre: int = Query(-100, description="风格：-100=全部"),
    limit: int | None = Query(None, ge=1, le=100),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_singer_max", env.DEFAULT_HOME_BLOCK_MAX)
    items = await service.chart_singers(area, sex, genre, limit)
    return {"ok": True, "items": items}


@router.get("/singer/profile")
async def singer_profile(singer_mid: str = Query(..., min_length=1)) -> dict[str, Any]:
    data = await service.singer_profile(singer_mid)
    return {"ok": True, **data}


@router.get("/singer/songs")
async def singer_songs(
    singer_mid: str = Query(..., min_length=1),
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    items = await service.singer_songs(singer_mid, limit)
    return {"ok": True, "items": items}


@router.get("/singer/albums")
async def singer_albums(
    singer_mid: str = Query(..., min_length=1),
    limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    items = await service.singer_albums(singer_mid, limit)
    return {"ok": True, "items": items}


# --------------------------------------------------------------------------
# 5. 热搜
# --------------------------------------------------------------------------
@router.get("/search/hotkey")
async def search_hotkey(limit: int | None = Query(None, ge=1, le=100)) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_hotkey_max", env.DEFAULT_HOME_BLOCK_MAX)
    items = await service.hot_keys(limit)
    return {"ok": True, "items": items}


# --------------------------------------------------------------------------
# 6. 每日30首
# --------------------------------------------------------------------------
@router.get("/daily/songs")
async def daily_songs(
    limit: int | None = Query(None, ge=1, le=100),
    songlist_id: int = Query(0, ge=0, description="自定义歌单 ID；0=自动挑一个公开日推歌单"),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_daily_max", env.DEFAULT_HOME_BLOCK_MAX)
    data = await service.daily_songs(limit, songlist_id)
    return {"ok": True, **data}


# --------------------------------------------------------------------------
# 7. 相似歌曲
# --------------------------------------------------------------------------
@router.get("/song/similar")
async def song_similar(
    songmid: str = Query("", description="种子歌曲 mid（与 songid 二选一）"),
    songid: int = Query(0, ge=0, description="种子歌曲数字 ID（与 songmid 二选一）"),
    limit: int | None = Query(None, ge=1, le=100),
) -> dict[str, Any]:
    if limit is None:
        limit = _limit("home_similar_max", env.DEFAULT_HOME_BLOCK_MAX)
    data = await service.similar_songs(songid, songmid.strip(), limit)
    return {"ok": True, **data}


# --------------------------------------------------------------------------
# 8. 歌曲卡片增强（收藏数 / 评论数 / 标签 / 热评）
# --------------------------------------------------------------------------
@router.get("/song/stats")
async def song_stats(
    songmid: str = Query("", description="歌曲 mid（与 songid 二选一）"),
    songid: int = Query(0, ge=0),
) -> dict[str, Any]:
    data = await service.song_stats(songmid.strip(), songid)
    return {"ok": True, **data}


@router.get("/song/byid")
async def song_by_id(
    songid: int = Query(..., ge=1, description="歌曲数字 ID（首页信息流卡片只带这个）"),
) -> dict[str, Any]:
    data = await service.song_detail_by_id(songid)
    return {"ok": True, "song": data}


# --------------------------------------------------------------------------
# 9. 我的收藏
# --------------------------------------------------------------------------
@router.get("/favourite/list")
async def favourite_list(
    kind: str = Query("song", pattern="^(song|songlist|album)$"),
    page: int = Query(1, ge=1),
    num: int | None = Query(None, ge=1, le=100),
) -> dict[str, Any]:
    if not store.is_logged_in():
        raise errors.NotLoggedInError()
    if num is None:
        num = _limit("home_fav_max", env.DEFAULT_HOME_BLOCK_MAX)
    data = await service.favourite(kind, page, num)
    return {"ok": True, **data}
