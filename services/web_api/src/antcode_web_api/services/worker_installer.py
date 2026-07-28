"""Worker 安装脚本读取与安全命令生成。"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from importlib.resources import files
from urllib.parse import urlparse

from antcode_core.common.config import Settings

_INSTALL_ASSET_PACKAGE = "antcode_web_api.install_assets"
_INSTALL_SCRIPTS = {"linux": "install_worker.sh", "macos": "install_worker.sh", "windows": "install_worker.ps1"}
_PINNED_GIT_REF = re.compile(r"^[0-9a-fA-F]{40}$")
_PINNED_TOOL_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class WorkerInstallerConfigurationError(ValueError):
    """Worker 安装分发配置无效。"""


@dataclass(frozen=True)
class WorkerInstallConfig:
    api_base_url: str
    source_url: str
    source_ref: str
    gateway_endpoint: str
    gateway_tls: bool
    uv_version: str


@dataclass(frozen=True)
class WorkerInstallCommandRequest:
    os_type: str
    install_key: str


def read_install_script(script_name: str) -> bytes:
    """读取包内固定安装脚本。"""
    if script_name not in set(_INSTALL_SCRIPTS.values()):
        raise ValueError(f"未知 Worker 安装脚本: {script_name}")
    return files(_INSTALL_ASSET_PACKAGE).joinpath(script_name).read_bytes()


def install_script_sha256(script_name: str) -> str:
    return hashlib.sha256(read_install_script(script_name)).hexdigest()


def load_worker_install_config(settings: Settings) -> WorkerInstallConfig:
    """读取并验证生成安装命令所需的部署配置。"""
    api_base_url = settings.public_api_base_url
    source_url = settings.WORKER_INSTALL_SOURCE_URL.strip()
    source_ref = settings.WORKER_INSTALL_SOURCE_REF.strip()
    _validate_api_base_url(api_base_url)
    _validate_source_url(source_url)
    if not _PINNED_GIT_REF.fullmatch(source_ref):
        raise WorkerInstallerConfigurationError("WORKER_INSTALL_SOURCE_REF 必须是完整的 40 位 Git commit")
    uv_version = settings.WORKER_INSTALL_UV_VERSION.strip()
    if not _PINNED_TOOL_VERSION.fullmatch(uv_version):
        raise WorkerInstallerConfigurationError("WORKER_INSTALL_UV_VERSION 必须是固定的三段版本号")
    return WorkerInstallConfig(
        api_base_url=api_base_url,
        source_url=source_url,
        source_ref=source_ref.lower(),
        gateway_endpoint=_gateway_endpoint(settings),
        gateway_tls=settings.WORKER_INSTALL_GATEWAY_TLS,
        uv_version=uv_version,
    )


def build_worker_install_command(
    request: WorkerInstallCommandRequest,
    config: WorkerInstallConfig,
) -> str:
    script_name = _INSTALL_SCRIPTS.get(request.os_type)
    if script_name is None:
        raise ValueError(f"不支持的 Worker 操作系统: {request.os_type}")
    digest = install_script_sha256(script_name)
    script_url = f"{config.api_base_url}/api/v1/workers/{_public_script_name(script_name)}"
    if request.os_type == "windows":
        return _build_powershell_command(request.install_key, script_url, digest, config)
    return _build_unix_command(request.install_key, script_url, digest, config)


def _validate_api_base_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WorkerInstallerConfigurationError("API_BASE_URL 必须是有效的 HTTP(S) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WorkerInstallerConfigurationError("API_BASE_URL 不允许包含凭证、query 或 fragment")
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        # P2 §4.6: 生产未显式配置 API_BASE_URL 时，public_api_base_url 会由
        # SERVER_DOMAIN 推导成 HTTP，走到这里必然失败。错误信息必须给出
        # 根因与修复方式，而不是让"安装必失败"看起来像随机故障。
        raise WorkerInstallerConfigurationError(
            f"远程 Worker 安装必须通过 HTTPS API_BASE_URL（当前: {value}）。"
            "生产部署请显式设置 API_BASE_URL=https://<对外域名>；"
            "未显式配置时该值由 SERVER_DOMAIN 推导为 HTTP，仅适用于本机开发。"
        )


def _validate_source_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WorkerInstallerConfigurationError("WORKER_INSTALL_SOURCE_URL 必须是有效的 HTTPS Git 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WorkerInstallerConfigurationError("WORKER_INSTALL_SOURCE_URL 不允许包含凭证、query 或 fragment")


def _gateway_endpoint(settings: Settings) -> str:
    host = settings.GATEWAY_HOST.strip()
    if not host or "://" in host or any(char.isspace() for char in host):
        raise WorkerInstallerConfigurationError("GATEWAY_HOST 必须是有效的主机名或 IP")
    normalized_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{normalized_host}:{settings.GATEWAY_PORT}"


def _public_script_name(asset_name: str) -> str:
    return "install.ps1" if asset_name.endswith(".ps1") else "install.sh"


def _build_unix_command(key: str, url: str, digest: str, config: WorkerInstallConfig) -> str:
    env_values = _unix_environment(key, config)
    return (
        'tmp="$(mktemp)" && trap \'rm -f "$tmp"\' EXIT && '
        f'curl --fail --silent --show-error --location --output "$tmp" {shlex.quote(url)} && '
        f"if command -v sha256sum >/dev/null 2>&1; then printf '%s  %s\\n' {shlex.quote(digest)} \"$tmp\" "
        "| sha256sum -c -; else printf '%s  %s\\n' "
        f'{shlex.quote(digest)} "$tmp" | shasum -a 256 -c -; fi && {env_values} bash "$tmp"'
    )


def _unix_environment(key: str, config: WorkerInstallConfig) -> str:
    values = {
        "ANTCODE_WORKER_KEY": key,
        "WORKER_API_BASE_URL": config.api_base_url,
        "WORKER_GATEWAY_ENDPOINT": config.gateway_endpoint,
        "WORKER_GATEWAY_TLS": str(config.gateway_tls).lower(),
        "WORKER_CREDENTIAL_STORE": "persistent",
        "WORKER_INSTALL_SOURCE_URL": config.source_url,
        "WORKER_INSTALL_SOURCE_REF": config.source_ref,
        "WORKER_INSTALL_UV_VERSION": config.uv_version,
    }
    return " ".join(f"{name}={shlex.quote(value)}" for name, value in values.items())


def _build_powershell_command(key: str, url: str, digest: str, config: WorkerInstallConfig) -> str:
    assignments = _powershell_environment(key, config)
    return (
        "$tmp = Join-Path $env:TEMP (([IO.Path]::GetRandomFileName()) + '.ps1'); try { "
        f"Invoke-WebRequest -Uri {_ps_quote(url)} -OutFile $tmp; "
        f"if ((Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLowerInvariant() -ne {_ps_quote(digest)}) "
        "{ throw 'Worker installer SHA256 verification failed' }; "
        f"{assignments} & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tmp; "
        'if ($LASTEXITCODE -ne 0) { throw "Worker installer exited with code $LASTEXITCODE" } } finally { '
        "Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue; "
        f"{_powershell_environment_cleanup()} }}"
    )


def _powershell_environment(key: str, config: WorkerInstallConfig) -> str:
    values = {
        "ANTCODE_WORKER_KEY": key,
        "WORKER_API_BASE_URL": config.api_base_url,
        "WORKER_GATEWAY_ENDPOINT": config.gateway_endpoint,
        "WORKER_GATEWAY_TLS": str(config.gateway_tls).lower(),
        "WORKER_CREDENTIAL_STORE": "persistent",
        "WORKER_INSTALL_SOURCE_URL": config.source_url,
        "WORKER_INSTALL_SOURCE_REF": config.source_ref,
        "WORKER_INSTALL_UV_VERSION": config.uv_version,
    }
    return " ".join(f"$env:{name} = {_ps_quote(value)};" for name, value in values.items())


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_environment_cleanup() -> str:
    names = (
        "ANTCODE_WORKER_KEY",
        "WORKER_API_BASE_URL",
        "WORKER_GATEWAY_ENDPOINT",
        "WORKER_GATEWAY_TLS",
        "WORKER_CREDENTIAL_STORE",
        "WORKER_INSTALL_SOURCE_URL",
        "WORKER_INSTALL_SOURCE_REF",
        "WORKER_INSTALL_UV_VERSION",
    )
    return "; ".join(f"Remove-Item Env:{name} -ErrorAction SilentlyContinue" for name in names)
