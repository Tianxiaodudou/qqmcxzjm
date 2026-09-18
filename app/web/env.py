"""运行时配置：全部来自生命周期脚本导出的环境变量。"""

from __future__ import annotations

import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return value if value else default


def _env_int(key: str, default: int, low: int = 0, high: int = 600000) -> int:
    """读取整数环境变量，非法值回落默认，并夹在 [low, high] 内。"""
    try:
        number = int(str(_env(key, str(default))).strip())
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


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
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
LOGS_FILE = DATA_DIR / "logs.json"
PUSH_LOG_FILE = DATA_DIR / "push_log.json"

# 音质：不再由用户选择，自动按从高到低尝试，取登录账号可用的最高音质
# 消息推送默认服务地址（用户可在「设置 → 消息推送」里覆盖）
DEFAULT_PUSH_BASE = _env("QQMUSIC_PUSH_BASE", "http://192.168.1.29:818")
DEFAULT_PUSH_TOKEN = _env("QQMUSIC_PUSH_TOKEN", "")

QUALITY_ORDER = ("master", "flac", "ogg_320", "mp3_320", "acc_192", "mp3_128")
# 兜底音质：仅当上面全部不可用时使用
DEFAULT_QUALITY = _env("QQMUSIC_QUALITY", "mp3_320")
DEFAULT_LYRIC_TRANS = _env("QQMUSIC_LYRIC_TRANS", "true").lower() in {"1", "true", "yes", "on"}

# 下载节奏默认值（安装/配置向导可覆盖：wizard_interval_min_ms / wizard_interval_max_ms）
DEFAULT_INTERVAL_MIN_MS = _env_int("QQMUSIC_INTERVAL_MIN_MS", 300, low=0, high=60000)
DEFAULT_INTERVAL_MAX_MS = _env_int("QQMUSIC_INTERVAL_MAX_MS", 800, low=0, high=60000)
# 「全选」单次最多选中的歌曲数：点全选时前端最多把整张列表补全到这么多首
DEFAULT_SELECT_MAX = _env_int("QQMUSIC_SELECT_MAX", 500, low=10, high=20000)
# 首页推荐数量上限（默认值）：下限由 QQ 服务器实际推送数量决定，这里只做“最多显示多少”的截断
DEFAULT_HOME_SONGLISTS_MAX = _env_int("QQMUSIC_HOME_SONGLISTS_MAX", 20, low=1, high=60)
DEFAULT_HOME_NEWSONGS_MAX = _env_int("QQMUSIC_HOME_NEWSONGS_MAX", 30, low=1, high=100)
DEFAULT_HOME_GUESS_MAX = _env_int("QQMUSIC_HOME_GUESS_MAX", 30, low=1, high=60)
DEFAULT_HOME_RADAR_MAX = _env_int("QQMUSIC_HOME_RADAR_MAX", 50, low=1, high=100)
# 首页新增板块的数量上限（默认值）：每块都只是「最多显示多少」，服务器给多少就显示多少
DEFAULT_HOME_BLOCK_MAX = _env_int("QQMUSIC_HOME_BLOCK_MAX", 30, low=1, high=100)
DEFAULT_HOME_FEED_MAX = _env_int("QQMUSIC_HOME_FEED_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_CHART_MAX = _env_int("QQMUSIC_HOME_CHART_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_NEWALBUM_MAX = _env_int("QQMUSIC_HOME_NEWALBUM_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_MV_MAX = _env_int("QQMUSIC_HOME_MV_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_HOTKEY_MAX = _env_int("QQMUSIC_HOME_HOTKEY_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_DAILY_MAX = _env_int("QQMUSIC_HOME_DAILY_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_SIMILAR_MAX = _env_int("QQMUSIC_HOME_SIMILAR_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
DEFAULT_HOME_FAV_MAX = _env_int("QQMUSIC_HOME_FAV_MAX", DEFAULT_HOME_BLOCK_MAX, low=1, high=100)
# 「每日30首」用的歌单：0=自动挑一个公开日推歌单（官方那个是登录后的私人歌单，匿名拿不到）
DEFAULT_DAILY_SONGLIST_ID = _env_int("QQMUSIC_DAILY_SONGLIST_ID", 0, low=0, high=10**12)

# 完整元数据：额外拉取制作人名单 / 榜单标签 / 收藏热度等（多 3 次请求）
DEFAULT_META_FULL = _env("QQMUSIC_META_FULL", "true").lower() in {"1", "true", "yes", "on"}
# 是否在音频同目录额外保存同名 .json 元数据文件
DEFAULT_META_JSON = _env("QQMUSIC_META_JSON", "false").lower() in {"1", "true", "yes", "on"}

# 用户授权可访问的目录（fnOS 提供，冒号分隔）
AUTHORIZED_PATHS = [p for p in _env("QQMUSIC_AUTHORIZED_PATHS", "").split(":") if p]

# 飞牛应用网关（trim_open_gateway）接入参数。
# App Token 不在此读取：每次调用时从进程环境动态读取 TRIM_API_TOKEN。
TRIM_APP_NAME = _env("QQMUSIC_TRIM_APP_NAME", "qqmusic-downloader")
TRIM_API_SOCKET = _env("QQMUSIC_TRIM_API_SOCKET", "/var/run/trim_open_gateway_apiscope.socket")
TRIM_API_PATH = _env("QQMUSIC_TRIM_API_PATH", "/api/v1/trimapp")
try:
    TRIM_API_TIMEOUT = float(_env("QQMUSIC_TRIM_API_TIMEOUT", "8"))
except ValueError:
    TRIM_API_TIMEOUT = 8.0

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
    "master": "臻品母带",
    "flac": "SQ 无损 FLAC",
    "ogg_320": "HQ 高品质 OGG",
    "mp3_320": "HQ 高品质 MP3 320K",
    "acc_192": "HQ 高品质 AAC 192K",
    "mp3_128": "标准音质 MP3 128K",
}


def quality_label(quality: str) -> str:
    return QUALITY_LABELS.get(quality, quality)
