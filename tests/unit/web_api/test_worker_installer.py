import hashlib
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models import User, WorkerInstallKey
from antcode_web_api.routes.v1 import v1_router
from antcode_web_api.routes.v1 import workers as workers_route
from antcode_web_api.routes.v1.worker_install import (
    download_unix_worker_installer,
    download_windows_worker_installer,
)
from antcode_web_api.services.worker_installer import (
    WorkerInstallCommandRequest,
    WorkerInstallConfig,
    WorkerInstallerConfigurationError,
    build_worker_install_command,
    load_worker_install_config,
    read_install_script,
)
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

PINNED_REF = "0123456789abcdef0123456789abcdef01234567"


def _write_executable(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_python() -> str:
    return """#!/usr/bin/env bash
set -eu
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python"
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ] && [ "${3:-}" = "install" ]; then
  uv_bin="$(dirname "$0")/uv"
  printf '%s\n' '#!/usr/bin/env bash' 'if [ "${1:-}" = "--version" ]; then exit 0; fi' \
    'printf "%s\\n" "$*" >>"$FAKE_UV_CAPTURE"' 'exit 0' >"$uv_bin"
  chmod 755 "$uv_bin"
  exit 0
fi
exit 0
"""


def _fake_git() -> str:
    return """#!/usr/bin/env bash
set -eu
if [ "$1" = "init" ]; then mkdir -p "$3/.git"; exit 0; fi
repo="$2"
case "$3" in
  remote) exit 0 ;;
  fetch) printf '%s\n' "$8" >"$repo/FETCH_HEAD" ;;
  rev-parse) cat "$repo/FETCH_HEAD" ;;
  checkout) exit 0 ;;
esac
"""


def _config() -> WorkerInstallConfig:
    return WorkerInstallConfig(
        api_base_url="https://control.example.com",
        source_url="https://github.com/xyuns-cc/AntCode.git",
        source_ref=PINNED_REF,
        gateway_endpoint="gateway.example.com:50051",
        gateway_tls=True,
        uv_version="0.8.17",
    )


@pytest.mark.parametrize(
    ("response", "asset_name"),
    [
        (download_unix_worker_installer(), "install_worker.sh"),
        (download_windows_worker_installer(), "install_worker.ps1"),
    ],
)
def test_installer_endpoint_digest_matches_exact_body(response, asset_name: str) -> None:
    expected = read_install_script(asset_name)
    assert response.body == expected
    assert response.headers["x-content-sha256"] == hashlib.sha256(expected).hexdigest()
    assert response.headers["x-content-type-options"] == "nosniff"


def test_installer_routes_are_available_under_worker_api() -> None:
    app = FastAPI()
    app.include_router(v1_router)
    client = TestClient(app)
    assert client.get("/workers/install.sh").status_code == 200
    assert client.get("/workers/install.ps1").status_code == 200


def test_unix_command_downloads_verifies_then_executes() -> None:
    request = WorkerInstallCommandRequest(os_type="linux", install_key="KEY'001")
    command = build_worker_install_command(request, _config())
    assert "https://control.example.com/api/v1/workers/install.sh" in command
    assert "sha256sum -c -" in command
    assert "shasum -a 256 -c -" in command
    assert 'bash "$tmp"' in command
    assert "curl -sSL" not in command
    assert "| bash" not in command
    assert "KEY'\"'\"'001" in command
    assert "50051/api" not in command


def test_powershell_command_downloads_verifies_then_executes() -> None:
    request = WorkerInstallCommandRequest(os_type="windows", install_key="KEY'001")
    command = build_worker_install_command(request, _config())
    assert "https://control.example.com/api/v1/workers/install.ps1" in command
    assert "Get-FileHash" in command
    assert "KEY''001" in command
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tmp" in command
    assert "Invoke-Expression" not in command
    assert "iex" not in command.lower()


def test_install_scripts_perform_real_pinned_workspace_install() -> None:
    unix_script = read_install_script("install_worker.sh").decode()
    windows_script = read_install_script("install_worker.ps1").decode()
    for script in (unix_script, windows_script):
        assert "WORKER_INSTALL_SOURCE_REF" in script
        assert "git" in script
        assert "--all-packages" in script
        assert "--frozen" in script
        assert "antcode_worker run" in script
        assert "uv==" in script


def test_unix_installer_completes_real_control_flow_with_pinned_source(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "python3", _fake_python())
    _write_executable(fake_bin / "git", _fake_git())
    capture = tmp_path / "uv-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "ANTCODE_WORKER_KEY": "KEY001",
        "WORKER_API_BASE_URL": "https://control.example.com",
        "WORKER_GATEWAY_ENDPOINT": "gateway.example.com:50051",
        "WORKER_GATEWAY_TLS": "true",
        "WORKER_INSTALL_SOURCE_URL": "https://github.com/xyuns-cc/AntCode.git",
        "WORKER_INSTALL_SOURCE_REF": PINNED_REF,
        "WORKER_INSTALL_UV_VERSION": "0.8.17",
        "FAKE_UV_CAPTURE": str(capture),
    }
    script = "services/web_api/src/antcode_web_api/install_assets/install_worker.sh"
    result = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    calls = capture.read_text(encoding="utf-8")
    assert "sync --directory" in calls
    assert "--all-packages --frozen" in calls
    assert "antcode_worker run" in calls
    saved_config = (tmp_path / "home/.antcode/worker-src/.antcode-worker.env").read_text(encoding="utf-8")
    assert "ANTCODE_WORKER_KEY" not in saved_config
    assert "WORKER_CREDENTIAL_STORE=persistent" in saved_config


def test_install_config_rejects_missing_or_mutable_source_ref() -> None:
    settings = SimpleNamespace(
        public_api_base_url="https://control.example.com",
        WORKER_INSTALL_SOURCE_URL="https://github.com/xyuns-cc/AntCode.git",
        WORKER_INSTALL_SOURCE_REF="main",
        WORKER_INSTALL_GATEWAY_TLS=True,
        WORKER_INSTALL_CONFIG_REQUIRED=False,
        WORKER_INSTALL_UV_VERSION="0.8.17",
        GATEWAY_HOST="gateway.example.com",
        GATEWAY_PORT=50051,
    )
    with pytest.raises(WorkerInstallerConfigurationError, match="Git commit"):
        load_worker_install_config(settings)


def test_install_config_rejects_cleartext_remote_api() -> None:
    settings = SimpleNamespace(
        public_api_base_url="http://control.example.com:8000",
        WORKER_INSTALL_SOURCE_URL="https://github.com/xyuns-cc/AntCode.git",
        WORKER_INSTALL_SOURCE_REF=PINNED_REF,
        WORKER_INSTALL_GATEWAY_TLS=False,
        WORKER_INSTALL_CONFIG_REQUIRED=False,
        WORKER_INSTALL_UV_VERSION="0.8.17",
        GATEWAY_HOST="gateway.example.com",
        GATEWAY_PORT=50051,
    )
    with pytest.raises(WorkerInstallerConfigurationError, match="HTTPS API_BASE_URL"):
        load_worker_install_config(settings)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"WORKER_INSTALL_GATEWAY_TLS": False}, "WORKER_INSTALL_GATEWAY_TLS"),
        ({"GATEWAY_HOST": "gateway.example.com/path"}, "GATEWAY_HOST"),
        ({"GATEWAY_PORT": 0}, "GATEWAY_PORT"),
        ({"GATEWAY_PORT": 65536}, "GATEWAY_PORT"),
    ],
)
def test_install_config_rejects_insecure_or_invalid_gateway(overrides, error: str) -> None:
    values = {
        "public_api_base_url": "https://control.example.com",
        "WORKER_INSTALL_SOURCE_URL": "https://github.com/xyuns-cc/AntCode.git",
        "WORKER_INSTALL_SOURCE_REF": PINNED_REF,
        "WORKER_INSTALL_GATEWAY_TLS": True,
        "WORKER_INSTALL_CONFIG_REQUIRED": False,
        "WORKER_INSTALL_UV_VERSION": "0.8.17",
        "GATEWAY_HOST": "gateway.example.com",
        "GATEWAY_PORT": 50051,
    }
    values.update(overrides)

    with pytest.raises(WorkerInstallerConfigurationError, match=error):
        load_worker_install_config(SimpleNamespace(**values))


def test_required_install_config_rejects_loopback_http_api() -> None:
    settings = SimpleNamespace(
        public_api_base_url="http://127.0.0.1:8000",
        WORKER_INSTALL_SOURCE_URL="https://github.com/xyuns-cc/AntCode.git",
        WORKER_INSTALL_SOURCE_REF=PINNED_REF,
        WORKER_INSTALL_GATEWAY_TLS=True,
        WORKER_INSTALL_CONFIG_REQUIRED=True,
        WORKER_INSTALL_UV_VERSION="0.8.17",
        GATEWAY_HOST="gateway.example.com",
        GATEWAY_PORT=50051,
    )

    with pytest.raises(WorkerInstallerConfigurationError, match="HTTPS API_BASE_URL"):
        load_worker_install_config(settings)


@pytest.mark.asyncio
async def test_generate_key_fails_before_persisting_when_distribution_is_unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(
        User,
        "get_or_none",
        AsyncMock(return_value=SimpleNamespace(is_active=True, is_admin=True)),
    )
    create_key = AsyncMock()
    monkeypatch.setattr(WorkerInstallKey, "persist_install_key", create_key)
    error = WorkerInstallerConfigurationError("WORKER_INSTALL_SOURCE_URL 缺失")

    def fail_config(_settings):
        raise error

    monkeypatch.setattr(workers_route, "load_worker_install_config", fail_config)
    request = workers_route.WorkerInstallKeyRequest(os_type="linux")
    current_user = SimpleNamespace(user_id=1)

    with pytest.raises(HTTPException) as exc_info:
        await workers_route.generate_install_key(request, SimpleNamespace(), current_user)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Worker 安装分发未正确配置"
    assert "WORKER_INSTALL_SOURCE_URL" not in exc_info.value.detail
    create_key.assert_not_awaited()
