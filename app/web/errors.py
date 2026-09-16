"""统一错误映射：把 QQMusicApi 的各类异常收敛成本项目的三类失败原因。"""

from __future__ import annotations

import logging

from qqmusic_api.core import exceptions as qm_exc

logger = logging.getLogger("qqmusic.errors")

# 失败原因分类
CREDENTIAL_EXPIRED = "credential_expired"
NETWORK = "network"
RATELIMITED = "ratelimited"
OTHER = "other"

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
)

# 限流类异常：QQ 音乐在请求过于频繁（尤其未登录）时返回
_RATELIMITED_ERRORS = tuple(
    cls for cls in (getattr(qm_exc, "RatelimitedError", None),) if isinstance(cls, type)
)

_CREDENTIAL_HINTS = ("credential", "登录已过期", "凭证", "登录过期", "refresh token", "musickey")
_RATELIMITED_HINTS = ("ratelimit", "操作频繁", "频率", "too many", "频繁")


def classify(exc: BaseException) -> str:
    """把异常归类为 credential_expired / ratelimited / network / other。"""
    if isinstance(exc, _CREDENTIAL_ERRORS):
        return CREDENTIAL_EXPIRED
    if isinstance(exc, _NETWORK_ERRORS):
        return NETWORK
    if _RATELIMITED_ERRORS and isinstance(exc, _RATELIMITED_ERRORS):
        return RATELIMITED
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(hint.lower() in text for hint in _RATELIMITED_HINTS):
        return RATELIMITED
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return NETWORK
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(hint.lower() in text for hint in _CREDENTIAL_HINTS):
        return CREDENTIAL_EXPIRED
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
