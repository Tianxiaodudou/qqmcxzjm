"""FastAPI 应用装配：路由、静态 UI、全局异常处理、生命周期。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import env, errors, security
from .context import manager, service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("qqmusic.app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("QQ音乐下载器服务启动，数据目录 %s", env.DATA_DIR)
    try:
        await manager.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务队列启动失败：%s", security.sanitize_log(str(exc)))
    try:
        yield
    finally:
        await manager.stop()
        await service.close()
        logger.info("QQ音乐下载器服务已停止")


app = FastAPI(
    title="QQ音乐下载器",
    version=env.APP_VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# 全局异常处理：凭证过期统一处理，其它错误统一错误码
# --------------------------------------------------------------------------
@app.exception_handler(errors.LoginExpiredError)
async def _handle_login_expired(_: Request, exc: errors.LoginExpiredError) -> JSONResponse:
    logger.info("登录已过期，已清空凭证")
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "code": exc.code, "message": exc.message},
    )


@app.exception_handler(errors.AppError)
async def _handle_app_error(_: Request, exc: errors.AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "code": exc.code, "message": exc.message},
    )


@app.exception_handler(Exception)
async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    reason = errors.classify(exc)
    safe_message = security.sanitize_log(f"{type(exc).__name__}: {exc}")
    logger.warning("未预期错误：%s", safe_message[:500])
    if reason == errors.CREDENTIAL_EXPIRED:
        from . import store

        store.clear_credentials()
        return JSONResponse(
            status_code=401,
            content={"ok": False, "code": errors.CREDENTIAL_EXPIRED, "message": "登录已过期，请重新登录"},
        )
    return JSONResponse(
        status_code=500,
        content={"ok": False, "code": "internal_error", "message": "服务内部错误，请稍后重试"},
    )


# --------------------------------------------------------------------------
# API 路由
# --------------------------------------------------------------------------
from .routers_music import router as music_router  # noqa: E402
from .routers_tasks import router as tasks_router  # noqa: E402

app.include_router(music_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    from . import store

    return {
        "ok": True,
        "version": env.APP_VERSION,
        "gateway_prefix": env.GATEWAY_PREFIX,
        "logged_in": store.is_logged_in(),
    }


# --------------------------------------------------------------------------
# 静态 UI（同时兼容网关前缀与直连访问）
# --------------------------------------------------------------------------
_UI_DIR = env.UI_DIR if env.UI_DIR.exists() else Path(__file__).resolve().parent.parent / "ui"


async def index():
    """返回前端入口页面。"""
    return FileResponse(_UI_DIR / "index.html")


async def favicon():
    """返回应用图标。"""
    return FileResponse(_UI_DIR / "images" / "icon_64.png")


# 同时兼容「网关前缀访问」与「直连根路径访问」两种方式
_prefixes: list[str] = []
for _candidate in (env.GATEWAY_PREFIX, ""):
    if _candidate not in _prefixes:
        _prefixes.append(_candidate)

for _prefix in _prefixes:
    _suffix = _prefix.strip("/").replace("/", "-") or "root"
    app.mount(
        f"{_prefix}/static",
        StaticFiles(directory=str(_UI_DIR)),
        name=f"ui-static-{_suffix}",
    )
    app.add_api_route(
        f"{_prefix}/",
        index,
        methods=["GET"],
        name=f"ui-index-{_suffix}",
        include_in_schema=False,
    )
    app.add_api_route(
        f"{_prefix}/favicon.ico",
        favicon,
        methods=["GET"],
        name=f"ui-favicon-{_suffix}",
        include_in_schema=False,
    )
