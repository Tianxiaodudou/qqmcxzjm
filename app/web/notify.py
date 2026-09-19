"""消息推送（飞牛统一推送服务）：

- 请求：POST {base}/api/push，头 `Authorization: Bearer <token>`，体 `{"title","content","type"}`；
- base / token 由用户在「设置 → 消息推送」里填写，留空则只记日志不推送；
- 标题格式固定：`QQ音乐下载器-<事件类型>`（见 EVENT_LABELS）；
- 消息类型（type）由设置 push_type 决定：text / markdown / html，三种排版各自优化；
- 内容长度上限 5000 字符，超长自动截断（MAX_CONTENT）；
- 聚合推送：每类事件的「数量阈值」（设置 push_batch_*_count，1 = 每条即时推送）
  决定累计多少条明细才推送一次，推送时把累计明细汇总成一条（不再按分钟去重）；
- 批次推送：一批下载任务全部结束（成功/失败都算）后额外推送一条「任务完成」通知；
- 每次事件都会写一条消息日志，每次推送（成功或失败）都会写一条推送记录；
- 推送在后台线程里排队执行，队列排空后线程自动退出（不常驻）。
"""

from __future__ import annotations

import html as _html
import queue
import threading
import time
from datetime import datetime
from typing import Any

from . import env, store

# 单条推送 content 的字符上限（需求：最多 5000 个字符，排版时注意）
MAX_CONTENT = 5000
# 单条推送最多汇总多少条明细（极端刷屏保护，多余的用一句话概括）
MAX_ITEMS = 200

# 事件类型 → 中文标签（标题固定为 "QQ音乐下载器-<标签>"）
EVENT_LABELS = {
    "success": "下载成功",
    "duplicate": "已有同名文件",
    "failed": "下载失败",
    "expired": "登录态过期",
    "batch": "任务完成",
    "test": "测试通知",
}

_LEVEL_BY_EVENT = {
    "success": "success",
    "duplicate": "warn",
    "failed": "error",
    "expired": "error",
    "batch": "info",
    "test": "info",
}

# 事件 → 「数量阈值」设置键（累计多少条明细推送一次；1 = 每条即时推送）
BATCH_SETTING_KEYS = {
    "success": "push_batch_success_count",
    "failed": "push_batch_fail_count",
    "expired": "push_batch_expire_count",
}
BATCH_DEFAULTS = {"success": 1, "failed": 1, "expired": 1}

VALID_TYPES = ("text", "markdown", "html")

# 聚合缓冲：事件 → 还没推出去的明细列表（跨事件独立计数）
_BUFFERS: dict[str, list[str]] = {}
_BUFFER_LOCK = threading.Lock()


def batch_threshold(event: str) -> int:
    """该事件的「数量阈值」：累计多少条明细推送一次（1~1000，默认 1）。"""
    key = BATCH_SETTING_KEYS.get(event)
    if not key:
        return 1
    try:
        number = int(float(store.load_settings().get(key)))
    except (TypeError, ValueError):
        number = BATCH_DEFAULTS.get(event, 1)
    return max(1, min(1000, number))


_QUEUE: "queue.Queue[dict[str, Any]]" = queue.Queue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()


def push_config() -> dict[str, Any]:
    settings = store.load_settings()
    kind = str(settings.get("push_type") or "text").strip().lower()
    if kind not in VALID_TYPES:
        kind = "text"
    return {
        "base": str(settings.get("push_base") or "").strip(),
        "token": str(settings.get("push_token") or "").strip(),
        "type": kind,
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


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str) -> str:
    """保证 content 不超过 MAX_CONTENT 字符（超出部分截断并注明）。"""
    text = str(text or "")
    if len(text) <= MAX_CONTENT:
        return text
    note = "\n…（内容过长，已截断）"
    return text[: MAX_CONTENT - len(note)] + note


def _esc(value: Any) -> str:
    return _html.escape(str(value), quote=False)


def render_content(title: str, items: list[str]) -> str:
    """按设置里的消息类型把明细排版成推送 content（并保证长度不超限）。"""
    kind = push_config()["type"]
    shown = [str(item) for item in items[:MAX_ITEMS]]
    hidden = len(items) - len(shown)
    foot = f"共 {len(items)} 条 · {_stamp()}" if len(items) > 1 else _stamp()
    if hidden > 0:
        foot = f"{foot}（仅列出前 {len(shown)} 条，其余 {hidden} 条略）"
    if kind == "markdown":
        body = [f"# {title}", ""]
        body += [f"- {item}" for item in shown]
        body += ["", f"> {foot}"]
        text = "\n".join(body)
    elif kind == "html":
        rows = "".join(f"<li>{_esc(item)}</li>" for item in shown)
        text = (
            f"<h3>{_esc(title)}</h3><ul>{rows}</ul>"
            f'<p style="color:#888;font-size:12px">{_esc(foot)}</p>'
        )
    else:
        body = [title, ""]
        body += [f"· {item}" for item in shown]
        body += ["", foot]
        text = "\n".join(body)
    return _clip(text)


def send(title: str, content: str, kind: str = "") -> dict[str, Any]:
    """同步调用一次推送接口（测试通知也走这里）。"""
    config = push_config()
    if not config["base"] or not config["token"]:
        return {"ok": False, "error": "未配置推送服务地址或 Token", "status": 0}
    kind = (kind or config["type"] or "text").strip().lower()
    if kind not in VALID_TYPES:
        kind = "text"
    payload = {"title": title, "content": _clip(content or ""), "type": kind}
    try:
        import httpx  # noqa: PLC0415 本地导入，避免无网络环境下的启动开销

        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                _url(config["base"]),
                headers={"Authorization": f"Bearer {config['token']}"},
                json=payload,
            )
        ok = 200 <= response.status_code < 300
        return {
            "ok": ok,
            "status": response.status_code,
            "error": "" if ok else response.text[:300],
        }
    except Exception as exc:  # noqa: BLE001 网络异常不应影响下载流程
        return {"ok": False, "status": 0, "error": str(exc)[:300]}


def _worker() -> None:
    """推送队列消费者：队列排空即退出，不留常驻线程。"""
    global _WORKER
    while True:
        try:
            job = _QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            result = send(job["title"], job.get("content") or "", job.get("type") or "")
            ok = bool(result.get("ok"))
            store.append_push_record(
                {
                    "event": job.get("event") or "",
                    "title": job["title"],
                    "ok": ok,
                    "status": int(result.get("status") or 0),
                    "error": result.get("error") or "",
                    "time": int(time.time()),
                }
            )
        except Exception as exc:  # noqa: BLE001
            store.append_push_record(
                {
                    "event": job.get("event") or "",
                    "title": job["title"],
                    "ok": False,
                    "status": 0,
                    "error": str(exc)[:300],
                    "time": int(time.time()),
                }
            )
    with _WORKER_LOCK:
        _WORKER = None


def _dispatch(job: dict[str, Any]) -> None:
    global _WORKER
    with _WORKER_LOCK:
        _QUEUE.put(job)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker, name="notify-push", daemon=True)
            _WORKER.start()


def _push_summary(event: str, items: list[str]) -> None:
    """把一批明细汇总成一条推送（标题按事件类型固定，内容按消息类型排版）。"""
    title = f"QQ音乐下载器-{EVENT_LABELS.get(event, event)}"
    content = render_content(title, items)
    _dispatch({"event": event, "title": title, "content": content, "dedup_key": ""})


def _enqueue_event(event: str, items: list[str], enabled_push: bool = True) -> dict[str, Any]:
    """记录日志 + 按数量阈值聚合推送。返回本次事件的摘要。"""
    items = [str(item) for item in items if str(item).strip()]
    level = _LEVEL_BY_EVENT.get(event, "info")
    for item in items:
        store.append_log(level, event, item, "")
    if not items:
        return {"event": event, "logged": False, "pushed": False, "reason": "没有可推送内容"}
    if not enabled_push:
        return {"event": event, "logged": True, "pushed": False, "reason": "该事件推送已关闭"}
    config = push_config()
    if not config["base"] or not config["token"]:
        return {"event": event, "logged": True, "pushed": False, "reason": "未配置推送服务"}
    threshold = batch_threshold(event)
    if threshold <= 1:
        _push_summary(event, items)
        return {"event": event, "logged": True, "pushed": True, "count": len(items)}
    with _BUFFER_LOCK:
        buffer = _BUFFERS.setdefault(event, [])
        buffer.extend(items)
        if len(buffer) < threshold:
            return {
                "event": event,
                "logged": True,
                "pushed": False,
                "buffered": len(buffer),
                "threshold": threshold,
            }
        pending, _BUFFERS[event] = buffer[:], []
    _push_summary(event, pending)
    return {"event": event, "logged": True, "pushed": True, "count": len(pending)}


def flush_all() -> int:
    """把各事件缓冲里剩余的明细立即汇总推送（一批任务结束时调用）。"""
    with _BUFFER_LOCK:
        pending = {key: value[:] for key, value in _BUFFERS.items() if value}
        _BUFFERS.clear()
    for event, items in pending.items():
        _push_summary(event, items)
    return len(pending)


def _task_identity(task: Any) -> tuple[str, str, str]:
    """从下载任务（dataclass / 字典）取出展示名、详情与标识。"""
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
    """下载成功：进入 success 聚合缓冲（累计到数量阈值就汇总推送一次）。"""
    label, fallback, _key = _task_identity(task)
    extra = detail or fallback
    text = f"下载成功：{label}"
    if extra and extra not in text:
        text = f"{text}（{extra}）"
    return _enqueue_event("success", [text], push_config()["on_success"])


def notify_task_failure(task: Any, detail: str = "") -> dict[str, Any]:
    """下载失败：进入 failed 聚合缓冲。"""
    label, fallback, _key = _task_identity(task)
    return notify_failure(label, detail or fallback)


def notify_failure(name: str, detail: str = "", dedup_key: str = "") -> dict[str, Any]:
    """单条「下载失败」（dedup_key 仅为兼容旧调用保留，已不再去重）。"""
    text = f"下载失败：{name}"
    if detail:
        text = f"{text} —— {detail}"
    return _enqueue_event("failed", [text], push_config()["on_fail"])


def notify_duplicate(name: str, detail: str = "", dedup_key: str = "") -> dict[str, Any]:
    """单条「已有同名文件」（兼容旧调用；批量创建请用 notify_duplicates）。"""
    text = f"已有同名文件：{name}"
    if detail:
        text = f"{text}（{detail}）"
    return _enqueue_event("duplicate", [text], push_config()["on_dup"])


def notify_duplicates(skipped: list[Any]) -> dict[str, Any]:
    """批量创建任务时一次性汇总「已有同名文件」（不再每首单独推送）。"""
    items: list[str] = []
    for entry in skipped or []:
        if isinstance(entry, dict):
            name = str(
                entry.get("name") or entry.get("songmid") or entry.get("song_mid") or "未知歌曲"
            )
            detail = str(
                entry.get("output_name") or entry.get("path") or entry.get("detail") or ""
            )
        else:
            name, detail = str(entry), ""
        items.append(f"已有同名文件：{name}（{detail}）" if detail else f"已有同名文件：{name}")
    return _enqueue_event("duplicate", items, push_config()["on_dup"])


def notify_login_expired(detail: str = "") -> dict[str, Any]:
    """登录态过期：进入 expired 聚合缓冲。"""
    text = "QQ音乐登录态已过期，请重新登录"
    if detail:
        text = f"{text}（{detail}）"
    return _enqueue_event("expired", [text], push_config()["on_expire"])


def notify_batch_done(success: int, fails: list[Any]) -> dict[str, Any]:
    """一批任务全部结束后的「任务完成」通知：成功/失败数量 + 失败原因。"""
    fails = list(fails or [])
    items = [f"成功 {int(success)} 首"]
    if fails:
        items.append(f"失败 {len(fails)} 首")
        for entry in fails:
            if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                name, reason = str(entry[0]), str(entry[1] or "")
            elif isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("title") or "未知歌曲")
                reason = str(entry.get("reason") or entry.get("detail") or "")
            else:
                name, reason = str(entry), ""
            items.append(f"失败：{name} —— {reason or '原因未知'}")
    else:
        items.append("本批任务全部成功")
    return _enqueue_event("batch", items, True)


def send_test() -> dict[str, Any]:
    """设置页「发送测试通知」：同步发送并记录结果。"""
    if not enabled():
        store.append_log("warn", "test", "测试通知未发送", "请先填写推送服务地址与 Token")
        return {"ok": False, "error": "请先填写推送服务地址与 Token", "status": 0}
    title = f"QQ音乐下载器-{EVENT_LABELS['test']}"
    content = render_content(
        title,
        [
            f"这是一条测试通知，来自 {env.APP_VERSION}",
            f"消息类型：{push_config()['type']}",
            f"发送时间：{_stamp()}",
        ],
    )
    result = send(title, content)
    store.append_push_record(
        {
            "event": "test",
            "title": title,
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
