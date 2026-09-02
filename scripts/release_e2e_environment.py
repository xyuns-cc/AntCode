"""Shared ephemeral material for the production-Compose release E2E gates.

仓库没有自动化发布流水线，唯一入口是 `infra/docker/run-gateway-e2e.sh`（五个应用
镜像由 `docker compose build` 就地构建）。这里集中收敛 PKI、密钥、网络布局与
Compose 变量：如果将来另接一条镜像来源不同的发布通道（例如按 registry digest
拉取），它必须复用本模块，否则"测试机验过的画像"与"实际发布的画像"会悄悄分叉。
"""

from __future__ import annotations

import json
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from scripts.release_e2e_pki import DEFAULT_WORKER_TLS_DIR, write_release_pki

#: 由 Compose 本地构建的五个应用镜像；生产 Compose 里它们只有 tag，没有 digest。
APP_SERVICES = ("web-api", "master", "gateway", "worker", "frontend")
#: 第三方运行时镜像，仍按 digest pin——这部分不可变性没有随发布流水线一起消失。
RUNTIME_SERVICES = ("postgres", "redis", "reverse-proxy")
IMAGE_NAMESPACE = "antcode"
IMAGE_PATTERN = re.compile(r"\S+@sha256:[0-9a-f]{64}")

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

#: 编排层读取的机群描述（slug + Worker 数量），与 Compose 变量面分开存放。
FLEET_FILE = "fleet.json"
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
#: scheme 的默认端口。等于默认端口时 origin 不带端口，与 URL 规范化一致。
DEFAULT_PORTS = MappingProxyType({"https": DEFAULT_HTTPS_PORT, "http": DEFAULT_HTTP_REDIRECT_PORT})
#: 宿主侧访问名。容器侧必须用 CONTAINER_HOST_ALIAS，两者不可互换（见 host_git_base_url）。
RUNNER_HOST = "localhost"
DEFAULT_UV_VERSION = "0.8.17"
DEFAULT_REDIS_HOST_PORT = 16379
#: 容器侧统一用它称呼宿主；`prod.e2e-control.yml` / `prod.e2e-worker.yml` 里的
#: `extra_hosts: host-gateway` 负责解析。宿主进程解析不了它（见 host_git_base_url）。
CONTAINER_HOST_ALIAS = "host.docker.internal"
#: TEST-NET-1 的 discard 端口：UDP connect 只让内核按路由表挑出源地址，不发任何报文。
ROUTE_PROBE_ENDPOINT = ("192.0.2.1", 9)


def _origin(host: str, scheme: str, port: int) -> str:
    if port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def runner_origin(scheme: str, port: int) -> str:
    """宿主侧（pytest 与发布门禁脚本）访问本轮反向代理的 origin。

    端口必须是本轮 Compose 真正发布出去的那个。门禁每一步都要打在它上面，否则判据
    分不清"本轮起的栈"和"共享测试机上别人占着的 :443"——写死 localhost 默认值正是
    875e6dd 在 [3/7] 清掉的那个形状。
    """
    return _origin(RUNNER_HOST, scheme, port)


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
    worker_count: int = 1

    @property
    def public_api_origin(self) -> str:
        return _origin(CONTAINER_HOST_ALIAS, "https", self.https_port)

    @property
    def runner_api_origin(self) -> str:
        return runner_origin("https", self.https_port)


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


def worker_tls_directory(index: int) -> str:
    return DEFAULT_WORKER_TLS_DIR if index == 0 else f"{DEFAULT_WORKER_TLS_DIR}-{index}"


def worker_variables(root: Path, slug: str, index: int) -> dict[str, str]:
    """第 ``index`` 个 Worker 的独占变量。

    容器名 / 数据卷 / mTLS 目录 / 安装 Key 文件必须逐个 Worker 独立：Compose 把它们
    全部参数化了，多 Worker 就是同一份 Compose 起多个 project + 换这一组变量。
    index 0 不加后缀，单 Worker 拓扑的命名与既有部署完全一致。
    """
    suffix = "" if index == 0 else f"-{index}"
    return {
        "ANTCODE_WORKER_NAME": f"{slug}-worker{suffix}",
        "ANTCODE_WORKER_CONTAINER_NAME": f"antcode-{slug}-worker{suffix}",
        "ANTCODE_WORKER_DATA_VOLUME": f"antcode-{slug}-worker{suffix}-data",
        "ANTCODE_WORKER_TLS_DIR": str(root / worker_tls_directory(index)),
        "ANTCODE_WORKER_INSTALL_KEY_FILE": str(root / f"secrets/worker_install_key{suffix}"),
    }


def _static_variables(settings: ReleaseE2ESettings) -> dict[str, str]:
    slug = settings.worker_slug
    return {
        **worker_variables(settings.root, slug, 0),
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
        "ANTCODE_REDIS_ACL_DIR": "redis",
        "ANTCODE_GATEWAY_TLS_DIR": "gateway-tls",
        "ANTCODE_TLS_CERTS_DIR": "public-tls",
    }
    return {name: str(root / relative) for name, relative in paths.items()}


def application_images(tag: str) -> dict[str, str]:
    """Compose 就地构建的五个镜像引用；它们不在任何 registry 上，只有 tag 可用。"""
    return {service: f"{IMAGE_NAMESPACE}-{service}:{tag}" for service in APP_SERVICES}


def runtime_images(lock_path: Path) -> dict[str, str]:
    """读取第三方运行时镜像的 digest 锁；schema 或引用不合规一律拒绝。"""
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime image lock has an invalid schema")
    images: object = payload.get("images")
    if payload.get("schema_version") != 1 or not isinstance(images, dict) or set(images) != set(RUNTIME_SERVICES):
        raise ValueError("runtime image lock has an invalid schema")
    for name in RUNTIME_SERVICES:
        reference = images[name]
        if not isinstance(reference, str) or IMAGE_PATTERN.fullmatch(reference) is None:
            raise ValueError(f"runtime image is not digest locked: {name}")
    return {name: images[name] for name in RUNTIME_SERVICES}


def _image_variables(tag: str, runtime: dict[str, str]) -> dict[str, str]:
    variables: dict[str, str] = {"ANTCODE_IMAGE_TAG": tag}
    for name, reference in runtime.items():
        repository, digest = reference.rsplit("@sha256:", 1)
        key = name.upper().replace("-", "_")
        variables[f"ANTCODE_{key}_IMAGE_REPOSITORY"] = repository
        variables[f"ANTCODE_{key}_IMAGE_DIGEST"] = digest
    return variables


def worker_env_file(index: int) -> str:
    return f"worker-{index}.env"


def _write_worker_files(root: Path, slug: str, indexes: range) -> None:
    """每个 Worker 的安装 Key 占位文件与 env 片段。

    安装 Key 是一次性凭据，每个 Worker 各拿一把；Compose 的 ``secrets: file:`` 在解析
    阶段就要求文件存在，所以先占位，编排器注册前再写入真实的 Key。env 片段供 shell
    侧 `set -a; . worker-N.env` 使用——shell 环境优先级高于 ``--env-file``，这样命名
    规则只由 ``worker_variables`` 一处定义，清理脚本不会与编排器分叉。
    """
    for index in indexes:
        variables = worker_variables(root, slug, index)
        write(Path(variables["ANTCODE_WORKER_INSTALL_KEY_FILE"]), "pending")
        content = "".join(f"{name}={value}\n" for name, value in sorted(variables.items()))
        write(root / worker_env_file(index), content)


def write_environment(settings: ReleaseE2ESettings, tag: str, runtime: dict[str, str]) -> dict[str, str]:
    """写出一次性 PKI、密钥、Compose 环境与 Git 根目录，返回 runner 侧取值。"""
    root = settings.root
    indexes = range(settings.worker_count)
    write_release_pki(root, worker_directories=[worker_tls_directory(index) for index in indexes])
    values = _secret_values(root)
    _write_worker_files(root, settings.worker_slug, indexes)
    environment = {
        **_static_variables(settings),
        **_path_variables(root),
        **_image_variables(tag, runtime),
    }
    write(root / "production.env", "".join(f"{name}={value}\n" for name, value in sorted(environment.items())))
    write(root / "release-images.json", json.dumps({**application_images(tag), **runtime}, sort_keys=True))
    # 机群规模只属于编排层，不进 production.env——那是 Compose 的变量面，不该混入
    # 部署拓扑元数据。编排器从这里取唯一事实来源。
    write(root / FLEET_FILE, json.dumps({"slug": settings.worker_slug, "count": settings.worker_count}))
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
