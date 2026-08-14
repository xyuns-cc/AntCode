"""Shared ephemeral material for the production-Compose release E2E gates.

CI（`docker-release-e2e.yml`，镜像来自 GHCR release digest）与本地/测试机
（`infra/docker/run-gateway-e2e.sh`，镜像来自本机构建）共用同一套 PKI、密钥、
网络布局与 Compose 变量，只有镜像来源与运行端口不同。两条路径必须共享这里的
实现，否则"测试机验过的画像"与"发布流水线跑的画像"会悄悄分叉。
"""

from __future__ import annotations

import json
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path

from scripts.release_e2e_pki import write_release_pki

#: frontend / reverse-proxy 的 pin 地址必须落在各自网络的 ``ip_range`` 之外，否则先创建
#: 的容器会从动态池底部抢走它，首次部署直接 "Address already in use"。
NETWORK_LAYOUT = {
    "ANTCODE_CONTROL_SUBNET": "172.30.40.0/26",
    "ANTCODE_CONTROL_DYNAMIC_RANGE": "172.30.40.32/27",
    "ANTCODE_FRONTEND_CONTROL_IP": "172.30.40.2",
    "ANTCODE_EDGE_SUBNET": "172.30.40.64/26",
    "ANTCODE_EDGE_DYNAMIC_RANGE": "172.30.40.96/27",
    "ANTCODE_REVERSE_PROXY_EDGE_IP": "172.30.40.66",
}

FILE_MODE = 0o600
REDIS_ACL_FILE_MODE = 0o666
REDIS_ACL_DIR_MODE = 0o777
GIT_ROOT_MODE = 0o700
GIT_HTTP_PORT = 18081
SECRET_TOKEN_BYTES = 24
SALT_TOKEN_BYTES = 16
DEFAULT_GATEWAY_PUBLIC_PORT = 15051
DEFAULT_HTTPS_PORT = 443
DEFAULT_HTTP_REDIRECT_PORT = 80
DEFAULT_UV_VERSION = "0.8.17"
DEFAULT_POSTGRES_HOST_PORT = 15432
DEFAULT_REDIS_HOST_PORT = 16379
#: 容器侧统一用它称呼宿主；`prod.ci-control.yml` / `prod.ci-worker.yml` 里的
#: `extra_hosts: host-gateway` 负责解析。宿主进程解析不了它（见 host_git_base_url）。
CONTAINER_HOST_ALIAS = "host.docker.internal"
#: TEST-NET-1 的 discard 端口：UDP connect 只让内核按路由表挑出源地址，不发任何报文。
ROUTE_PROBE_ENDPOINT = ("192.0.2.1", 9)


@dataclass(frozen=True)
class ReleaseE2ESettings:
    """一次性发布 E2E 环境的全部可变量。"""

    root: Path
    source_url: str
    source_ref: str
    worker_slug: str
    gateway_public_port: int = DEFAULT_GATEWAY_PUBLIC_PORT
    https_port: int = DEFAULT_HTTPS_PORT
    http_redirect_port: int = DEFAULT_HTTP_REDIRECT_PORT
    uv_version: str = DEFAULT_UV_VERSION

    @property
    def public_api_origin(self) -> str:
        if self.https_port == DEFAULT_HTTPS_PORT:
            return f"https://{CONTAINER_HOST_ALIAS}"
        return f"https://{CONTAINER_HOST_ALIAS}:{self.https_port}"

    @property
    def runner_api_origin(self) -> str:
        if self.https_port == DEFAULT_HTTPS_PORT:
            return "https://localhost"
        return f"https://localhost:{self.https_port}"


def write(path: Path, value: str, mode: int = FILE_MODE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def host_git_base_url() -> str:
    """E2E Git 源地址，必须同时对宿主上的 pytest 与 Worker 容器有效。

    `host.docker.internal` 只在容器里靠 `extra_hosts` 解析，宿主上无人解析；
    回环地址反过来只在宿主上有效，容器里指向自己。两侧都能路由到的地址只有
    宿主自身的出口 IP，因此这里按路由表取它，而不是写死任何别名。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(ROUTE_PROBE_ENDPOINT)
        address = str(probe.getsockname()[0])
    return f"http://{address}:{GIT_HTTP_PORT}"


def _secret_values(root: Path) -> dict[str, str]:
    postgres_password = secrets.token_hex(SECRET_TOKEN_BYTES)
    redis_admin_password = secrets.token_hex(SECRET_TOKEN_BYTES)
    redis_health_password = secrets.token_hex(SECRET_TOKEN_BYTES)
    admin_password = f"E2e!{secrets.token_urlsafe(SECRET_TOKEN_BYTES)}Aa1"
    values = {
        "database_url": f"postgresql://antcode:{postgres_password}@postgres:5432/antcode_e2e_test",
        "redis_url": f"redis://antcode-admin:{redis_admin_password}@redis:6379/0",
        "postgres_password": postgres_password,
        "redis_healthcheck_password": redis_health_password,
        "encryption_key": secrets.token_urlsafe(SALT_TOKEN_BYTES * 3),
        "encryption_key_salt": secrets.token_hex(SALT_TOKEN_BYTES),
        "encryption_keys_legacy": "",
        "jwt_secret": secrets.token_urlsafe(SALT_TOKEN_BYTES * 3),
        "default_admin_password": admin_password,
        "worker_install_key": "pending",
    }
    for name, value in values.items():
        write(root / "secrets" / name, value)
    acl = (
        "user default off\n"
        f"user antcode-admin on >{redis_admin_password} ~* &* +@all\n"
        f"user health on >{redis_health_password} &* +ping\n"
    )
    write(root / "redis/users.acl", acl, REDIS_ACL_FILE_MODE)
    (root / "redis").chmod(REDIS_ACL_DIR_MODE)
    values["runner_redis_url"] = f"redis://antcode-admin:{redis_admin_password}@127.0.0.1:{DEFAULT_REDIS_HOST_PORT}/0"
    return values


def _static_variables(settings: ReleaseE2ESettings) -> dict[str, str]:
    slug = settings.worker_slug
    return {
        "POSTGRES_DB": "antcode_e2e_test",
        "POSTGRES_USER": "antcode",
        "REDIS_HEALTHCHECK_USER": "health",
        "REDIS_NAMESPACE": "antcode",
        "ANTCODE_GATEWAY_BIND_ADDRESS": "0.0.0.0",
        "ANTCODE_GATEWAY_HOST": CONTAINER_HOST_ALIAS,
        "ANTCODE_GATEWAY_PUBLIC_PORT": str(settings.gateway_public_port),
        "ANTCODE_PUBLIC_API_BASE_URL": settings.public_api_origin,
        "ANTCODE_WORKER_INSTALL_SOURCE_URL": settings.source_url,
        "ANTCODE_WORKER_INSTALL_SOURCE_REF": settings.source_ref,
        "ANTCODE_WORKER_INSTALL_UV_VERSION": settings.uv_version,
        **NETWORK_LAYOUT,
        "ANTCODE_TRUSTED_PROXIES": NETWORK_LAYOUT["ANTCODE_FRONTEND_CONTROL_IP"],
        "ANTCODE_WORKER_NAME": f"{slug}-worker",
        "ANTCODE_WORKER_CONTAINER_NAME": f"antcode-{slug}-worker",
        "ANTCODE_WORKER_DATA_VOLUME": f"antcode-{slug}-worker-data",
        "ANTCODE_WORKER_MEMORY": "4g",
        "ANTCODE_REDIS_MAXMEMORY": "256mb",
        "HTTPS_PORT": str(settings.https_port),
        "HTTP_REDIRECT_PORT": str(settings.http_redirect_port),
    }


def _path_variables(root: Path) -> dict[str, str]:
    paths = {
        "ANTCODE_DATABASE_URL_FILE": "secrets/database_url",
        "ANTCODE_REDIS_URL_FILE": "secrets/redis_url",
        "ANTCODE_POSTGRES_PASSWORD_FILE": "secrets/postgres_password",
        "ANTCODE_REDIS_HEALTHCHECK_PASSWORD_FILE": "secrets/redis_healthcheck_password",
        "ANTCODE_ENCRYPTION_KEY_FILE": "secrets/encryption_key",
        "ANTCODE_ENCRYPTION_KEY_SALT_FILE": "secrets/encryption_key_salt",
        "ANTCODE_ENCRYPTION_KEYS_LEGACY_FILE": "secrets/encryption_keys_legacy",
        "ANTCODE_JWT_SECRET_FILE": "secrets/jwt_secret",
        "ANTCODE_DEFAULT_ADMIN_PASSWORD_FILE": "secrets/default_admin_password",
        "ANTCODE_WORKER_INSTALL_KEY_FILE": "secrets/worker_install_key",
        "ANTCODE_REDIS_ACL_DIR": "redis",
        "ANTCODE_GATEWAY_TLS_DIR": "gateway-tls",
        "ANTCODE_WORKER_TLS_DIR": "worker-tls",
        "ANTCODE_TLS_CERTS_DIR": "public-tls",
    }
    return {name: str(root / relative) for name, relative in paths.items()}


def _image_variables(images: dict[str, str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for name, reference in images.items():
        repository, digest = reference.rsplit("@sha256:", 1)
        key = name.upper().replace("-", "_")
        variables[f"ANTCODE_{key}_IMAGE_REPOSITORY"] = repository
        variables[f"ANTCODE_{key}_IMAGE_DIGEST"] = digest
    return variables


def write_environment(settings: ReleaseE2ESettings, images: dict[str, str]) -> dict[str, str]:
    """写出一次性 PKI、密钥、Compose 环境与 Git 根目录，返回 runner 侧取值。"""
    root = settings.root
    write_release_pki(root)
    values = _secret_values(root)
    environment = {
        **_static_variables(settings),
        **_path_variables(root),
        **_image_variables(images),
    }
    write(root / "production.env", "".join(f"{name}={value}\n" for name, value in sorted(environment.items())))
    write(root / "release-images.json", json.dumps(images, sort_keys=True))
    (root / "git").mkdir(mode=GIT_ROOT_MODE)
    return values


def runner_environment(settings: ReleaseE2ESettings, values: dict[str, str]) -> dict[str, str]:
    """宿主上 `pytest tests/e2e` 需要的全部环境变量（不含 Worker ID）。"""
    return {
        "SSL_CERT_FILE": str(settings.root / "public-ca.crt"),
        "ANTCODE_E2E_CONFIRM": "FULL",
        "ANTCODE_E2E_ADMIN_USER": "admin",
        "ANTCODE_E2E_ADMIN_PASSWORD": values["default_admin_password"],
        "ANTCODE_E2E_WEB_API_URL": settings.runner_api_origin,
        "ANTCODE_E2E_EXPECT_TRANSPORT_MODE": "gateway",
        "ANTCODE_E2E_GIT_ROOT": str(settings.root / "git"),
        "ANTCODE_E2E_GIT_BASE_URL": host_git_base_url(),
        "ANTCODE_E2E_REDIS_URL": values["runner_redis_url"],
        "ANTCODE_E2E_REDIS_NAMESPACE": "antcode",
    }
