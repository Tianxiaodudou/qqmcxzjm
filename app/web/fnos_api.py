"""飞牛 fnOS 应用网关 API 最小客户端（trim_open_gateway_apiscope）。

设计约束：
- App Token 只在调用时从进程环境变量 ``TRIM_API_TOKEN`` 动态读取，不落盘、不下发前端、不进日志。
- 只允许白名单内的 req，应用不会退化成任意网关 API 的代理。
- 目录授权按 uid 隔离，uid 来自网关注入的请求头（``X-Trim-Userid``），永远不信任前端传值。
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import socket
import uuid
from typing import Any

from . import env

logger = logging.getLogger("qqmusic.fnos_api")

MAX_RESPONSE_BYTES = 1024 * 1024

# 只保留本项目真正使用的请求
ALLOWED_REQUESTS = {
    "trim.file.getUserAccessibleFolders",
    "trim.file.getSharedAccessibleFolders",
    "trim.file.checkUserACL",
    "trim.file.convertPath",
}


def _socket_path() -> str:
    return env.TRIM_API_SOCKET


def _token() -> str:
    return (os.environ.get("TRIM_API_TOKEN") or "").strip()


def available() -> bool:
    """网关是否可用：注入了 App Token 且 socket 存在。"""
    if not _token():
        return False
    try:
        return os.path.exists(_socket_path())
    except OSError:
        return False


class _UnixHTTPConnection(http.client.HTTPConnection):
    """通过 Unix Socket 访问飞牛网关 API。"""

    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:  # noqa: D102
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def _post_trim(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    conn = _UnixHTTPConnection(_socket_path(), timeout)
    try:
        conn.request(
            "POST",
            env.TRIM_API_PATH,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Authorization": f"Bearer {_token()}",
            },
        )
        response = conn.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES)
        if response.status != 200:
            raise RuntimeError(f"网关返回 HTTP {response.status}")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("网关返回格式异常")
        return data
    finally:
        conn.close()


async def call(req: str, data: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    """调用网关 API；失败不抛异常（授权查询失败不应阻断主流程）。

    返回 ``{"ok": bool, "code": int, "msg": str, "data": Any}``。
    """
    out: dict[str, Any] = {"ok": False, "code": -1, "msg": "", "data": None}
    if req not in ALLOWED_REQUESTS:
        out["msg"] = "该请求不在允许列表内"
        return out
    if not available():
        out["msg"] = "飞牛应用网关不可用"
        return out
    payload = {
        "reqId": uuid.uuid4().hex,
        "req": req,
        "appName": env.TRIM_APP_NAME,
        "data": data or {},
    }
    try:
        raw = await asyncio.to_thread(_post_trim, payload, float(timeout or env.TRIM_API_TIMEOUT))
    except Exception as exc:  # noqa: BLE001
        out["msg"] = f"网关调用失败({type(exc).__name__})"
        logger.warning("trim api 调用失败 req=%s err=%s", req, type(exc).__name__)
        return out
    if raw.get("code", -1) != 0:
        out["code"] = int(raw.get("code", -1))
        out["msg"] = str(raw.get("msg") or "网关返回业务错误")
        return out
    out.update(ok=True, code=0, msg="", data=raw.get("data"))
    return out


def _paths(data: Any) -> list[str]:
    """从网关返回中提取目录列表（兼容 {paths:[...]} 与 [...] 两种形态）。"""
    raw = data.get("paths") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    paths: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in paths:
            paths.append(text)
    return paths


async def user_authorized_dirs(uid: str) -> list[str] | None:
    """当前用户个人授权的目录；None 表示查询失败（调用方不应据此拒绝操作）。"""
    if not uid:
        return None
    res = await call("trim.file.getUserAccessibleFolders", {"uid": uid})
    return _paths(res.get("data")) if res["ok"] else None


async def shared_authorized_dirs() -> list[str] | None:
    """管理员共享授权的目录；None 表示查询失败。"""
    res = await call("trim.file.getSharedAccessibleFolders", {})
    return _paths(res.get("data")) if res["ok"] else None


async def check_user_acl(uid: str, path: str) -> bool | None:
    """校验用户对某个目录的 ACL；None 表示无法判定。"""
    if not uid or not path:
        return None
    res = await call("trim.file.checkUserACL", {"uid": uid, "path": path})
    if not res["ok"]:
        return None
    data = res.get("data")
    if isinstance(data, dict):
        for key in ("allowed", "hasPermission", "result", "ok", "access"):
            value = data.get(key)
            if isinstance(value, bool):
                return value
    return None
