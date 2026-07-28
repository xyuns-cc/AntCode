"""Tests for redacted E2E diagnostic annotations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from scripts.annotate_e2e_diagnostics import _escape, _load_secrets, _redact, _run


def test_load_and_redact_only_secret_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_WORKER_KEY", "worker-sensitive")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=db-sensitive\nREDIS_PASSWORD=cache-sensitive\nSERVER_DOMAIN=example.test\n",
        encoding="utf-8",
    )

    secrets = _load_secrets(env_file)
    redacted = _redact("db-sensitive cache-sensitive worker-sensitive example.test", secrets)

    assert set(secrets) == {"db-sensitive", "cache-sensitive", "worker-sensitive"}
    assert redacted == "*** *** *** example.test"


def test_escape_uses_github_workflow_command_encoding() -> None:
    assert _escape("first%\r\nsecond") == "first%25%0D%0Asecond"


@patch("scripts.annotate_e2e_diagnostics.subprocess.run")
def test_run_captures_failure_output_without_hiding_exit(mock_run: Mock) -> None:
    mock_run.return_value = Mock(stdout="out\n", stderr="err\n", returncode=1)

    output = _run(["docker", "compose", "ps", "-a"])

    assert "$ docker compose ps -a" in output
    assert "out\nerr" in output
    mock_run.assert_called_once_with(
        ["docker", "compose", "ps", "-a"],
        capture_output=True,
        text=True,
        check=False,
    )
