"""共享运行时上下文：解耦 main 与路由模块之间的循环导入。"""

from __future__ import annotations

from .downloader import DownloadManager
from .service import service

# 全局单例：任务管理器依赖 QQ 服务
manager = DownloadManager(service)

__all__ = ["manager", "service"]
