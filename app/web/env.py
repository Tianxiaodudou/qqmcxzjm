"""运行时配置：全部来自生命周期脚本导出的环境变量。"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value else default


APP_VERSION = _env("QQMUSIC_APP_VERSION", "1.0.0")
APP_DIR = Path(_env("QQMUSIC_APP_DIR", str(Path(__file__).resolve().parent.parent)))
UI_DIR = Path(_env("QQMUSIC_UI_DIR", str(APP_DIR / "ui")))
DATA_DIR = Path(_env("QQMUSIC_DATA_DIR", str(APP_DIR / "var")))
GATEWAY_PREFIX = _env("QQMUSIC_GATEWAY_PREFIX", "/app/qqmusic-downloader")
SOCKET_PATH = _env("QQMUSIC_SOCKET", str(APP_DIR / "app.sock"))

# 状态文件
CREDENTIAL_FILE = DATA_DIR / "credential.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
TASKS_FILE = DATA_DIR / "tasks.json"
HISTORY_FILE = DATA_DIR / "history.json"

# 默认音质
DEFAULT_QUALITY = _env("QQMUSIC_QUALITY", "mp3_320")
DEFAULT_LYRIC_TRANS = _env("QQMUSIC_LYRIC_TRANS", "true").lower() in {"1", "true", "yes", "on"}

# 用户授权可访问的目录（fnOS 提供，冒号分隔）
AUTHORIZED_PATHS = [p for p in _env("QQMUSIC_AUTHORIZED_PATHS", "").split(":") if p]

# 安装向导配置的下载目录
WIZARD_MEDIA_DIR = _env("QQMUSIC_MEDIA_DIR", "")

# 音质 -> (是否加密, 枚举名)
QUALITY_MAP = {
    "flac": ("encrypted", "FLAC"),
    "master": ("encrypted", "MASTER"),
    "ogg_320": ("encrypted", "OGG_320"),
    "mp3_320": ("plain", "MP3_320"),
    "mp3_128": ("plain", "MP3_128"),
    "acc_192": ("plain", "ACC_192"),
}

# 音质 -> Song.file 中对应的大小字段（用于展示/预估）
QUALITY_SIZE_KEYS = {
    "flac": "flac",
    "master": "",
    "ogg_320": "192ogg",
    "mp3_320": "320mp3",
    "mp3_128": "128mp3",
    "acc_192": "192aac",
}

QUALITY_LABELS = {
    "flac": "SQ 无损 FLAC（加密 .mflac）",
    "master": "臻品母带（加密 .mflac）",
    "ogg_320": "HQ 高品质 OGG（加密 .mgg）",
    "mp3_320": "HQ 高品质 MP3 320K",
    "mp3_128": "标准音质 MP3 128K",
    "acc_192": "HQ 高品质 AAC",
}


def quality_label(quality: str) -> str:
    return QUALITY_LABELS.get(quality, quality)
