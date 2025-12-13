"""
节点能力检测服务 - 检测本地环境的渲染能力并上报给主控
"""

import os
import platform
import shutil
from typing import Dict, Any, Optional

from loguru import logger


class CapabilityService:
    """节点能力检测服务"""

    def __init__(self):
        self._cached_capabilities: Optional[Dict[str, Any]] = None
        self._platform = platform.system().lower()  # windows, linux, darwin

    def detect_all(self, force_refresh: bool = False) -> Dict[str, Any]:
        """检测所有能力"""
        if self._cached_capabilities and not force_refresh:
            return self._cached_capabilities

        capabilities = {
            "drissionpage": self.detect_drissionpage(),
            "curl_cffi": self.detect_curl_cffi(),
        }

        self._cached_capabilities = capabilities
        logger.info(f"节点能力检测完成: {self._summarize(capabilities)}")
        return capabilities

    def _summarize(self, capabilities: Dict[str, Any]) -> str:
        """生成能力摘要"""
        enabled = []
        for name, cap in capabilities.items():
            if cap and cap.get("enabled"):
                extra = ""
                if name in ("drissionpage", "playwright", "selenium"):
                    headless = cap.get("headless", True)
                    extra = " (headless)" if headless else " (GUI)"
                enabled.append(f"{name}{extra}")
        return ", ".join(enabled) if enabled else "无渲染能力"

    def _get_default_headless(self) -> bool:
        """
        根据平台获取默认的 headless 设置
        - Linux 无 DISPLAY: 强制 headless
        - 其他: 默认 headless
        """
        if self._platform == "linux" and not os.getenv("DISPLAY"):
            return True
        return True

    def detect_drissionpage(self) -> Dict[str, Any]:
        """检测 DrissionPage 能力（自动检测，无需配置）"""
        headless = self._get_default_headless()

        result = {
            "enabled": False,
            "browser_path": None,
            "headless": headless,
            "headless_forced": self._platform == "linux" and not os.getenv("DISPLAY"),
            "platform": self._platform,
        }

        # 检查 DrissionPage 包是否安装
        try:
            from DrissionPage import ChromiumOptions  # noqa: F401
        except ImportError:
            result["error"] = "DrissionPage 未安装"
            return result

        # 检测浏览器路径
        browser_path = self._find_browser()
        if not browser_path:
            result["error"] = "未找到 Chrome/Chromium 浏览器"
            return result

        result["browser_path"] = browser_path
        result["enabled"] = True
        return result

    def detect_curl_cffi(self) -> Dict[str, Any]:
        """检测 curl_cffi 能力（自动检测，无需配置）"""
        result = {"enabled": False}

        try:
            from curl_cffi import requests as curl_requests  # noqa: F401
            result["enabled"] = True
        except ImportError:
            pass

        return result

    def _find_browser(self) -> Optional[str]:
        """查找 Chrome/Chromium 浏览器路径"""
        # 优先使用环境变量指定的路径
        env_path = os.getenv("DRISSIONPAGE_BROWSER_PATH")
        if env_path and os.path.isfile(env_path):
            return env_path

        # 常见浏览器路径
        browser_paths = [
            # Linux
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/snap/bin/chromium",
            # macOS
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            # Windows (通过 shutil.which)
            "chrome",
            "chromium",
            "google-chrome",
            "google-chrome-stable",
        ]

        for path in browser_paths:
            if path.startswith("/"):
                if os.path.isfile(path):
                    return path
            else:
                found = shutil.which(path)
                if found:
                    return found

        return None

    def has_render_capability(self) -> bool:
        """检查是否有渲染能力"""
        caps = self.detect_all()
        return caps.get("drissionpage", {}).get("enabled", False)


# 全局实例
capability_service = CapabilityService()
