"""统一错误映射：把 QQMusicApi 与下载流程的异常收敛为可读的失败原因。

下载失败不再统一落到 "other"：每个失败原因码都能在 REASON_TEXT 里查到中文提示，
前端 / 推送直接展示 ``reason_text(task.fail_reason)``。
"""

from __future__ import annotations

import logging

import httpx
from qqmusic_api.core import exceptions as qm_exc

logger = logging.getLogger("qqmusic.errors")

# ---- 失败原因分类（会原样返回给前端，前端按 REASON_TEXT 显示中文） ----
CREDENTIAL_EXPIRED = "credential_expired"  # 登录态过期
NOT_LOGGED_IN = "not_logged_in"  # 尚未登录
NETWORK = "network"  # 网络中断 / 超时
RATELIMITED = "ratelimited"  # 被接口限流
PAID_REQUIRED = "paid_required"  # 付费专辑 / 单曲，需单独购买
SONG_UNAVAILABLE = "song_unavailable"  # 详情返回空壳：无音源（付费专辑未购 / 下架 / 版权受限）
NO_PERMISSION = "no_permission"  # 无下载权限（版权或会员等级不足）
PREVIEW_ONLY = "preview_only"  # 只有试听片段
NO_URL = "no_url"  # 上游未返回播放地址
NO_AUDIO = "no_audio"  # 拿不到原始音频
EMPTY_AUDIO = "empty_audio"  # 下载内容为空
DECRYPT_FAILED = "decrypt_failed"  # 解密失败
TAG_FAILED = "tag_failed"  # 元数据写入失败
WRITE_FAILED = "write_failed"  # 成品文件落盘失败
CDN_UNAVAILABLE = "cdn_unavailable"  # CDN 链接 / 节点不可用
UPSTREAM = "upstream_error"  # 上游接口返回异常
OTHER = "other"  # 兜底（同样有中文提示）

# 失败原因码 → 中文提示（失败展示与推送文案的唯一来源）
REASON_TEXT: dict[str, str] = {
    CREDENTIAL_EXPIRED: "QQ音乐登录态已过期，请重新登录后重试",
    NOT_LOGGED_IN: "尚未登录 QQ音乐，登录后才能下载",
    NETWORK: "网络连接中断或超时，请检查网络后重试",
    RATELIMITED: "QQ音乐接口请求过于频繁，请稍后重试（登录后可减少限流）",
    PAID_REQUIRED: "该歌曲为付费内容（数字专辑或单曲），需要先在 QQ音乐购买后才能下载",
    SONG_UNAVAILABLE: "该歌曲暂无可用音源（常见于未购买的付费数字专辑，或已下架 / 版权受限）",
    NO_PERMISSION: "当前账号没有这首歌的下载权限（可能受版权或会员等级限制）",
    PREVIEW_ONLY: "该歌曲只能试听，无法下载完整版",
    NO_URL: "QQ音乐未返回这首歌的播放地址（可能已下架或受限）",
    NO_AUDIO: "没有拿到这首歌的原始音频数据，请重试",
    EMPTY_AUDIO: "下载到的音频内容为空，请重试",
    DECRYPT_FAILED: "音频解密失败，请重新登录后再试",
    TAG_FAILED: "音频已下载，但元数据（标签 / 封面 / 歌词）写入失败",
    WRITE_FAILED: "文件写入失败，请检查下载目录的磁盘空间与权限",
    CDN_UNAVAILABLE: "音频 CDN 链接不可用（可能已过期），请重试",
    UPSTREAM: "QQ音乐接口返回异常，请稍后重试",
    OTHER: "下载失败，原因未知，请重试；若持续失败请查看日志",
}

# AppError.code → 失败原因码
_APP_CODE_REASONS: dict[str, str] = {
    CREDENTIAL_EXPIRED: CREDENTIAL_EXPIRED,
    NOT_LOGGED_IN: NOT_LOGGED_IN,
    RATELIMITED: RATELIMITED,
    "no_permission": NO_PERMISSION,
    "no_url": NO_URL,
    "preview_only": PREVIEW_ONLY,
    "paid_required": PAID_REQUIRED,
    "song_unavailable": SONG_UNAVAILABLE,
    "cdn_unavailable": CDN_UNAVAILABLE,
    "decrypt_failed": DECRYPT_FAILED,
    "tag_failed": TAG_FAILED,
    "write_failed": WRITE_FAILED,
    "upstream_error": UPSTREAM,
}

_CREDENTIAL_ERRORS = (
    qm_exc.CredentialExpiredError,
    qm_exc.CredentialRefreshError,
    qm_exc.CredentialInvalidError,
    qm_exc.LoginAuthExpiredError,
)

_NETWORK_ERRORS = (
    qm_exc.NetworkError,
    qm_exc.TimeoutNetworkError,
    qm_exc.HTTPError,
    httpx.TransportError,
)

# 限流类异常：QQ 音乐在请求过于频繁（尤其未登录）时返回
_RATELIMITED_ERRORS = tuple(
    cls for cls in (getattr(qm_exc, "RatelimitedError", None),) if isinstance(cls, type)
)

_CREDENTIAL_HINTS = ("credential", "登录已过期", "凭证", "登录过期", "refresh token", "musickey")
_RATELIMITED_HINTS = ("ratelimit", "操作频繁", "频率", "too many", "频繁")


def reason_text(reason: str | None) -> str:
    """失败原因码 → 中文提示；未知原因也给一句可读的中文，绝不回退成英文码。"""
    if not reason:
        return ""
    return REASON_TEXT.get(str(reason), REASON_TEXT[OTHER])


def classify(exc: BaseException) -> str:
    """把异常归类为 REASON_TEXT 中的失败原因码（不再返回笼统的 other）。"""
    if isinstance(exc, AppError):
        mapped = _APP_CODE_REASONS.get(str(getattr(exc, "code", "") or ""))
        if mapped:
            return mapped
        # 错误码没登记（多为 internal_error / bad_request）：继续按文本嗅探，最后才落到 other
    elif isinstance(exc, _CREDENTIAL_ERRORS):
        return CREDENTIAL_EXPIRED
    elif isinstance(exc, _NETWORK_ERRORS):
        return NETWORK
    elif isinstance(exc, httpx.HTTPStatusError):
        return CDN_UNAVAILABLE
    elif _RATELIMITED_ERRORS and isinstance(exc, _RATELIMITED_ERRORS):
        return RATELIMITED
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(hint.lower() in text for hint in _RATELIMITED_HINTS):
        return RATELIMITED
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return NETWORK
    if any(hint.lower() in text for hint in _CREDENTIAL_HINTS):
        return CREDENTIAL_EXPIRED
    logger.debug("未识别的失败原因，按 other 处理：%s", text[:200])
    return OTHER


class AppError(Exception):
    """带统一错误码的应用异常，供 FastAPI 异常处理器使用。"""

    def __init__(self, message: str, code: str = "internal_error", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class LoginExpiredError(AppError):
    """登录过期：清空凭证并提示前端重新登录。"""

    def __init__(self, message: str = "登录已过期，请重新登录") -> None:
        super().__init__(message, code=CREDENTIAL_EXPIRED, status_code=401)


class NotLoggedInError(AppError):
    def __init__(self, message: str = "尚未登录") -> None:
        super().__init__(message, code="not_logged_in", status_code=401)


class BadRequestError(AppError):
    def __init__(self, message: str = "请求参数有误") -> None:
        super().__init__(message, code="bad_request", status_code=400)


class RatelimitedAppError(AppError):
    """被 QQ 音乐限流：未登录或请求过于频繁时出现，提示稍后重试或先登录。"""

    def __init__(
        self,
        message: str = "QQ音乐接口请求过于频繁，请稍后重试；登录后可减少限流",
    ) -> None:
        super().__init__(message, code=RATELIMITED, status_code=429)


class UpstreamError(AppError):
    """上游返回异常或数据缺失（拿不到地址/文件不完整等），属于可重试的失败。"""

    def __init__(
        self,
        message: str = "上游接口返回异常，请稍后重试",
        code: str = "upstream_error",
        status_code: int = 502,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class DownloadFailure(AppError):
    """下载流程内的失败：异常自带失败原因码，文案由 REASON_TEXT 统一给出。

    与 AppError 的区别：这里的原因码是给用户看的失败原因（如 paid_required），
    downloader 捕获后直接写入任务与历史记录的 fail_reason / fail_reason_text。
    """

    def __init__(self, reason: str = OTHER, message: str = "") -> None:
        self.reason = reason or OTHER
        super().__init__(message or reason_text(self.reason), code=self.reason, status_code=502)
