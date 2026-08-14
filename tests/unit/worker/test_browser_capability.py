from pathlib import Path

from antcode_worker.app import browser_capability


def test_playwright_requires_a_browser_executable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(browser_capability.shutil, "which", lambda _name: None)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert browser_capability.detect_playwright_capability() == {"enabled": False}


def test_playwright_managed_chromium_is_detected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(browser_capability.shutil, "which", lambda _name: None)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    executable = tmp_path / "chromium-123" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)

    assert browser_capability.detect_playwright_capability() == {"enabled": True}
