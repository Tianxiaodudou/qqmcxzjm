"""消息推送（飞牛统一推送服务）：

- 请求：POST {base}/api/push，头 `Authorization: Bearer <token>`，体 `{"title","content","type"}`；
- base / token 由用户在「设置 → 消息推送」里填写，留空则只记日志不推送；
- 事件：下载成功 / 已有同名文件 / 下载失败 / 登录态过期；
- 去重按「事件类别」：四类事件（下载成功 / 已有同名文件 / 下载失败 / 登录态过期）
  的去重窗口各自独立配置（设置里的 push_dedup_*_minutes，0 = 不去重）；
- 每次事件都会写一条消息日志，每次推送（成功或失败）都会写一条推送记录；
- 推送在后台线程里排队执行，队列排空后线程自动退出（不常驻）。
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from . import env, store

# 事件 → 对应的「同类事件去重分钟数」设置键（四类事件各自独立配置）
DEDUP_SETTING_KEYS = {
    "success": "push_dedup_success_minutes",
    "duplicate": "push_dedup_dup_minutes",
    "failed": "push_dedup_fail_minutes",
    "expired": "push_dedup_expire_minutes",
}

# 兜底去重窗口（秒）：设置文件里没有对应键时使用（test 事件从不去重）
DEDUP_WINDOWS = {
    "success": 180,
    "duplicate": 60,
    "failed": 60,
    "expired": 300,
    "test": 0,
}


def dedup_window(event: str) -> int:
    """该事件的去重窗口（秒）：读它自己的分钟设置（上限 24 小时），0 = 不去重。"""
    key = DEDUP_SETTING_KEYS.get(event)
    if not key:
        return 0
    try:
        raw = store.load_settings().get(key)
        if raw in (None, ""):
            return DEDUP_WINDOWS.get(event, 0)
        minutes = int(float(raw))
    except (TypeError, ValueError):
        return DEDUP_WINDOWS.get(event, 0)
    if minutes <= 0:
        return 0
    return min(24 * 60, minutes) * 60

# 去重登记表：key = "{event}:{dedup_key}"，值为最近一次推送时间（time.time()）
_LAST_PUSH: dict[str, float] = {}
_PUSH_LOCK = threading.Lock()


_LEVEL_BY_EVENT = {
    "success": "success",
    "duplicate": "warn",
    "failed": "error",
    "expired": "error",
    "test": "info",
}

_QUEUE: "queue.Queue[dict[str, Any]]" = queue.Queue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()


def push_config() -> dict[str, Any]:
    settings = store.load_settings()
    return {
        "base": str(settings.get("push_base") or "").strip(),
        "token": str(settings.get("push_token") or "").strip(),
        "on_success": bool(settings.get("push_on_success", True)),
        "on_dup": bool(settings.get("push_on_dup", True)),
        "on_fail": bool(settings.get("push_on_fail", True)),
        "on_expire": bool(settings.get("push_on_expire", True)),
    }


def enabled() -> bool:
    config = push_config()
    return bool(config["base"] and config["token"])


def _url(base: str) -> str:
    base = base.rstrip("/")
    return base + "/api/push"


def send(title: str, content: str, kind: str = "text") -> dict[str, Any]:
    """同步调用一次推送接口（测试通知也走这里）。"""
    config = push_config()
    if not config["base"] or not config["token"]:
        return {"ok": False, "error": "未配置推送服务地址或 Token", "status": 0}
    try:
        import httpx  # noqa: PLC0415 本地导入，避免无网络环境下的启动开销

        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                _url(config["base"]),
                headers={"Authorization": f"Bearer {config['token']}"},
                json={"title": title, "content": content, "type": kind},
            )
        ok = 200 <= response.status_code < 300
        return {
            "ok": ok,
            "status": response.status_code,
            "error": "" if ok else response.text[:300],
        }
    except Exception as exc:  # noqa: BLE001 网络异常不应影响下载流程
        return {"ok": False, "status": 0, "error": str(exc)[:300]}


def _should_push(event: str, dedup_key: str, window: int) -> bool:
    """去重判定：内存登记 + 落盘记录（进程重启后仍然生效）。

    判定通过时立即在内存里登记本次推送时间，避免紧接着的同类事件
    （背景线程还没来得及写推送记录）重复推送。
    """
    if window <= 0 or not dedup_key:
        return True
    key = f"{event}:{dedup_key}"
    now = time.time()
    with _PUSH_LOCK:
        last = max(_LAST_PUSH.get(key, 0.0), float(store.recent_push_time(key) or 0))
        if window and (now - last) < window:
            return False
        _LAST_PUSH[key] = now
    return True


def _worker() -> None:
    """推送队列消费者：队列排空即退出，不留常驻线程。"""
    global _WORKER
    while True:
        try:
            job = _QUEUE.get_nowait()
        except queue.Empty:
            with _WORKER_LOCK:
                _WORKER = None
            return
        try:
            result = send(job["title"], job["content"], job.get("type", "text"))
            store.append_push_record(
                {
                    "event": job["event"],
                    "title": job["title"],
                    "content": job["content"],
                    "dedup_key": job.get("dedup_key", ""),
                    "ok": bool(result.get("ok")),
                    "status": int(result.get("status") or 0),
                    "error": result.get("error") or "",
                    "time": int(time.time()),
                }
            )
        except Exception as exc:  # noqa: BLE001
            store.append_push_record(
                {
                    "event": job["event"],
                    "title": job["title"],
                    "ok": False,
                    "status": 0,
                    "error": str(exc)[:300],
                    "time": int(time.time()),
                }
            )


def _dispatch(job: dict[str, Any]) -> None:
    global _WORKER
    with _WORKER_LOCK:
        _QUEUE.put(job)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker, name="notify-push", daemon=True)
            _WORKER.start()


def notify_event(
    event: str,
    title: str,
    content: str,
    dedup_key: str = "",
    window: int | None = None,
) -> dict[str, Any]:
    """记录日志 + （按窗口去重后）排队推送。返回本次事件的摘要。"""
    level = _LEVEL_BY_EVENT.get(event, "info")
    store.append_log(level, event, title, content)
    if window is None:
        window = dedup_window(event)
    config = push_config()
    if not config["base"] or not config["token"]:
        return {"event": event, "logged": True, "pushed": False, "reason": "未配置推送服务"}
    if not _should_push(event, dedup_key, window):
        return {"event": event, "logged": True, "pushed": False, "reason": "去重窗口内"}
    _dispatch(
        {"event": event, "title": title, "content": content, "dedup_key": f"{event}:{dedup_key}" if dedup_key else ""}
    )
    return {"event": event, "logged": True, "pushed": True}


def _task_identity(task: Any) -> tuple[str, str, str]:
    """从下载任务（dataclass / 字典）取出展示名、详情与去重键。"""
    if isinstance(task, dict):
        get = lambda k, d="": task.get(k, d)  # noqa: E731
    else:
        get = lambda k, d="": getattr(task, k, d)  # noqa: E731
    name = str(get("name") or get("songmid") or "未知歌曲")
    singer = str(get("singer") or "")
    mid = str(get("songmid") or "")
    label = f"{name} - {singer}" if singer else name
    detail = str(get("output_name") or "")
    detail = f"文件：{detail}" if detail else "已完成并写入下载目录"
    return label, detail, mid or label


def notify_download_success(task: Any, detail: str = "") -> dict[str, Any]:
    """下载成功（第 8 项：按事件类别去重，不再按歌曲区分）。"""
    label, fallback, _key = _task_identity(task)
    name, detail = label, detail or fallback
    config = push_config()
    if not config["on_success"]:
        store.append_log("success", "success", f"下载成功：{name}", detail)
        return {"event": "success", "logged": True, "pushed": False, "reason": "该事件推送已关闭"}
    return notify_event("success", f"下载成功：{name}", detail, "success")


def notify_task_failure(task: Any, detail: str = "") -> dict[str, Any]:
    """下载失败（第 8 项：按事件类别去重）。"""
    label, fallback, _key = _task_identity(task)
    return notify_failure(label, detail or fallback, "failed")


def notify_duplicate(name: str, detail: str, dedup_key: str = "") -> dict[str, Any]:
    config = push_config()
    if not config["on_dup"]:
        store.append_log("warn", "duplicate", f"已有同名文件：{name}", detail)
        return {"event": "duplicate", "logged": True, "pushed": False, "reason": "该事件推送已关闭"}
    return notify_event("duplicate", f"已有同名文件：{name}", detail, dedup_key or "duplicate")


def notify_failure(name: str, detail: str, dedup_key: str = "") -> dict[str, Any]:
    config = push_config()
    if not config["on_fail"]:
        store.append_log("error", "failed", f"下载失败：{name}", detail)
        return {"event": "failed", "logged": True, "pushed": False, "reason": "该事件推送已关闭"}
    return notify_event("failed", f"下载失败：{name}", detail, dedup_key or "failed")


def notify_login_expired(detail: str = "") -> dict[str, Any]:
    config = push_config()
    if not config["on_expire"]:
        store.append_log("error", "expired", "QQ音乐登录态已过期，请重新登录", detail)
        return {"event": "expired", "logged": True, "pushed": False, "reason": "该事件推送已关闭"}
    return notify_event("expired", "QQ音乐登录态已过期", detail or "请到「下载器 → 左下角账号区」重新登录", dedup_key="expired")


def send_test() -> dict[str, Any]:
    """设置页「发送测试通知」：同步发送并记录结果。"""
    ok = enabled()
    if not ok:
        store.append_log("warn", "test", "测试通知未发送", "请先填写推送服务地址与 Token")
        return {"ok": False, "error": "请先填写推送服务地址与 Token", "status": 0}
    result = send("QQ音乐下载器测试通知", f"这是一条测试通知，来自 {env.APP_VERSION}，收到即表示推送配置可用。")
    store.append_push_record(
        {
            "event": "test",
            "title": "QQ音乐下载器测试通知",
            "content": "设置页测试通知",
            "dedup_key": "",
            "ok": bool(result.get("ok")),
            "status": int(result.get("status") or 0),
            "error": result.get("error") or "",
            "time": int(time.time()),
        }
    )
    store.append_log(
        "info" if result.get("ok") else "error",
        "test",
        "测试通知发送成功" if result.get("ok") else "测试通知发送失败",
        result.get("error") or f"HTTP {result.get('status')}",
    )
    return result
