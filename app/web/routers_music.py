"""登录、搜索、歌曲、歌单、推荐相关 API。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from . import env, errors, store
from .context import service

logger = logging.getLogger("qqmusic.api.music")
router = APIRouter(tags=["music"])


class QrRequest(BaseModel):
    type: str = "qq"


class QrCheckRequest(BaseModel):
    session_id: str


class PhoneRequest(BaseModel):
    phone: str
    country_code: int = 86


class PhoneLoginRequest(BaseModel):
    phone: str
    code: str


class UrlRequest(BaseModel):
    songmid: str
    quality: str = ""


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
@router.get("/status")
async def status() -> dict[str, Any]:
    return {
        "ok": True,
        "logged_in": service.status()["logged_in"],
    }


@router.post("/login/qrcode")
async def login_qrcode(payload: QrRequest) -> dict[str, Any]:
    data = await service.create_qrcode(payload.type)
    return {"ok": True, **data}


@router.post("/login/qrcode/check")
async def login_qrcode_check(payload: QrCheckRequest) -> dict[str, Any]:
    data = await service.check_qrcode(payload.session_id)
    return {"ok": True, **data}


@router.post("/login/sms")
async def login_sms(payload: PhoneRequest) -> dict[str, Any]:
    data = await service.send_authcode(payload.phone, payload.country_code)
    return {"ok": True, **data}


@router.post("/login/phone")
async def login_phone(payload: PhoneLoginRequest) -> dict[str, Any]:
    data = await service.phone_login(payload.phone, payload.code)
    return {"ok": True, **data}


@router.post("/login/logout")
async def logout() -> dict[str, Any]:
    await service.logout()
    return {"ok": True, "logged_in": False}


# --------------------------------------------------------------------------
# 搜索与推荐（未登录也可用部分接口）
# --------------------------------------------------------------------------
@router.get("/search")
async def search(
    keyword: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    num: int = Query(20, ge=1, le=50),
    type: str = Query("song"),
) -> dict[str, Any]:
    items = await service.search(keyword.strip(), type, page, num)
    return {"ok": True, "items": items, "page": page, "num": num}


def _home_limit(key: str, fallback: int) -> int:
    """首页推荐数量上限：优先用设置页里的值，读不到时退回内置默认。"""
    try:
        value = int(store.load_settings().get(key) or 0)
    except Exception:  # noqa: BLE001 —— 设置损坏不该拖垮首页
        value = 0
    return value or fallback


@router.get("/recommend/songlists")
async def recommend_songlists(
    page: int = Query(1, ge=1),
    num: int = Query(12, ge=1, le=30),
    limit: int | None = Query(None, ge=1, le=60, description="最多显示几个推荐歌单（缺省取设置）"),
) -> dict[str, Any]:
    if limit is None:
        limit = _home_limit("home_songlists_max", env.DEFAULT_HOME_SONGLISTS_MAX)
    items = await service.recommend_songlists(page, num, limit=limit)
    return {"ok": True, "items": items, "page": page}


@router.get("/recommend/newsongs")
async def recommend_newsongs(  # noqa: A002
    type: int = Query(5),
    limit: int | None = Query(None, ge=1, le=100, description="最多显示几首（缺省取设置）"),
) -> dict[str, Any]:
    if limit is None:
        limit = _home_limit("home_newsongs_max", env.DEFAULT_HOME_NEWSONGS_MAX)
    items = await service.recommend_newsongs(type, limit=limit)
    return {"ok": True, "items": items}


@router.get("/recommend/guess")
async def recommend_guess(
    limit: int | None = Query(None, ge=1, le=60, description="最多显示几首（缺省取设置）"),
) -> dict[str, Any]:
    """猜你喜欢：QQ音乐按当前账号推送（登录后即为个人化结果）。"""
    if limit is None:
        limit = _home_limit("home_guess_max", env.DEFAULT_HOME_GUESS_MAX)
    items = await service.recommend_guess(limit=limit)
    return {"ok": True, "items": items}


@router.get("/recommend/radar")
async def recommend_radar(
    limit: int | None = Query(None, ge=1, le=100, description="最多显示几首（缺省取设置）"),
) -> dict[str, Any]:
    """私人雷达（每日推荐）：QQ音乐每日按账号口味更新的个人电台。"""
    if limit is None:
        limit = _home_limit("home_radar_max", env.DEFAULT_HOME_RADAR_MAX)
    items = await service.recommend_radar(limit=limit)
    return {"ok": True, "items": items}


@router.get("/user/fav")
async def user_fav(
    page: int = Query(1, ge=1),
    num: int = Query(30, ge=1, le=50),
) -> dict[str, Any]:
    if not store.is_logged_in():
        raise errors.NotLoggedInError()
    items = await service.favourite_songs(page, num)
    return {"ok": True, "items": items}


@router.get("/user/songlists")
async def user_songlists() -> dict[str, Any]:
    if not store.is_logged_in():
        raise errors.NotLoggedInError()
    items = await service.favourite_songlists()
    return {"ok": True, "items": items}


# --------------------------------------------------------------------------
# 歌曲详情 / 歌词 / 试听直链
# --------------------------------------------------------------------------
@router.get("/song/{songmid}")
async def song_detail(songmid: str) -> dict[str, Any]:
    detail = await service.song_detail(songmid)
    if not detail:
        raise errors.AppError("未找到该歌曲", code="not_found", status_code=404)
    return {"ok": True, "song": detail}


@router.get("/song/{songmid}/lyric")
async def song_lyric(songmid: str, trans: int = Query(1)) -> dict[str, Any]:
    data = await service.song_lyric(songmid, trans=bool(trans))
    return {"ok": True, **data}


@router.post("/song/url")
async def song_url(payload: UrlRequest, quality: str = Query("")) -> dict[str, Any]:
    """预览播放用：只取直链，不创建下载任务。"""
    target_quality = payload.quality or quality or env.DEFAULT_QUALITY
    data = await service.preview_url(payload.songmid, target_quality)
    return {"ok": True, **data}


@router.get("/songlist/{songlist_id}")
async def songlist_detail(
    songlist_id: int,
    page: int = Query(1, ge=1),
    num: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    data = await service.songlist_detail(songlist_id, page, num)
    return {"ok": True, **data}
