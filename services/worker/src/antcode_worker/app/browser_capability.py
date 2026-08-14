"""Detect whether this Worker can launch a Chromium browser."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_SYSTEM_BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "msedge")
_PLAYWRIGHT_BROWSER_PATTERNS = (
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-linux64/chrome",
    "chromium_headless_shell-*/chrome-linux/headless_shell",
    "chromium_headless_shell-*/chrome-linux64/headless_shell",
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-win/chrome.exe",
)


def detect_playwright_capability() -> dict[str, bool]:
    """Report enabled only when both Playwright and a browser executable exist."""
    try:
        import playwright  # noqa: F401, PLC0415
    except ImportError:
        return {"enabled": False}
    if any(shutil.which(name) for name in _SYSTEM_BROWSER_NAMES):
        return {"enabled": True}
    browser_root = _playwright_browser_root()
    enabled = browser_root is not None and any(
        _has_executable(browser_root, pattern) for pattern in _PLAYWRIGHT_BROWSER_PATTERNS
    )
    return {"enabled": enabled}


def _playwright_browser_root() -> Path | None:
    configured = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if configured and configured != "0":
        return Path(configured).expanduser()
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA", "").strip()
        return Path(local_app_data) / "ms-playwright" if local_app_data else None
    return Path.home() / ".cache" / "ms-playwright"


def _has_executable(root: Path, pattern: str) -> bool:
    return any(path.is_file() and os.access(path, os.X_OK) for path in root.glob(pattern))


__all__ = ["detect_playwright_capability"]
