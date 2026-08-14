from unittest.mock import AsyncMock

import pytest
from antcode_worker.runtime import mise_bootstrap


@pytest.mark.asyncio
async def test_missing_mise_never_downloads_or_executes_installer(monkeypatch):
    missing = mise_bootstrap.MiseStatus(available=False, reason="missing")
    create_process = AsyncMock()
    monkeypatch.setattr(mise_bootstrap, "detect_mise", AsyncMock(return_value=missing))
    monkeypatch.setattr(mise_bootstrap.asyncio, "create_subprocess_exec", create_process)

    result = await mise_bootstrap.ensure_mise()

    assert result == missing
    create_process.assert_not_awaited()


def test_mise_bootstrap_has_no_remote_script_execution_path() -> None:
    source = mise_bootstrap.__file__
    text = open(source, encoding="utf-8").read()

    assert "curl -fsSL" not in text
    assert "| sh" not in text
    assert "ANTCODE_MISE_AUTO_INSTALL" not in text
