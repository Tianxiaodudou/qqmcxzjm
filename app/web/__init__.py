"""QQ音乐下载器 Web 服务包。"""

import sys as _sys
from pathlib import Path as _Path

# 内置第三方库（mutagen：用于把元数据写进音频文件），随应用一起打包，避免联网安装。
_VENDOR = _Path(__file__).resolve().parent.parent / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in _sys.path:  # pragma: no cover
    _sys.path.insert(0, str(_VENDOR))
