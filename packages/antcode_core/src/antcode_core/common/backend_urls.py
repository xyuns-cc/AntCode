"""后端连接串校验与各服务角色的后端边界。

从 ``common/config.py`` 抽出，原因有二：一是 ``Settings`` 已逼近文件行数上限，
二是"哪个角色允许持有哪些后端凭据"本身就是一条独立的安全边界，值得单独成文、
单独测试，而不是埋在配置类里。

边界矩阵（三者互斥，由 ``Settings.validate_backend_config`` 分派）：

===============  ===============  =====================================
角色              DATABASE_URL     REDIS_URL
===============  ===============  =====================================
控制面            必需              必需
Gateway Worker    禁止              禁止（无任何后端凭据）
Direct Worker     必需              禁止（改用 WORKER_REDIS_URL）
===============  ===============  =====================================
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

_DATABASE_SCHEMES = frozenset({"postgres", "postgresql"})
_REDIS_SCHEMES = frozenset(
    {
        # T6-T1: 支持集群 (`redis+cluster://`) 与哨兵 (`redis+sentinel://`)。
        # scheme 决定 create_async_redis_client 走哪条路径。
        "redis",
        "rediss",
        "redis+cluster",
        "rediss+cluster",
        "redis+sentinel",
        "rediss+sentinel",
    }
)


@dataclass(frozen=True)
class BackendUrls:
    """一次校验所需的全部输入，避免多参数在调用点错位。"""

    transport_mode: str
    database_url: str
    redis_url: str


def validate_database_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in _DATABASE_SCHEMES:
        raise ValueError("DATABASE_URL 只能使用 PostgreSQL 连接串。")
    if not parsed.hostname:
        raise ValueError("DATABASE_URL 缺少 host。")
    if not parsed.username:
        raise ValueError("DATABASE_URL 缺少 user。")
    if not parsed.password:
        raise ValueError("DATABASE_URL 缺少 password。")
    if not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL 缺少 database。")


def validate_redis_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in _REDIS_SCHEMES:
        raise ValueError(f"REDIS_URL scheme 不支持: {parsed.scheme!r}，仅允许 {sorted(_REDIS_SCHEMES)}")
    if not parsed.hostname:
        raise ValueError("REDIS_URL 缺少 host。")


def _require_database_url(database_url: str) -> None:
    if not database_url:
        raise ValueError("DATABASE_URL 必须设置，且只能使用 PostgreSQL。")
    validate_database_url(database_url)


def validate_gateway_worker_backends(urls: BackendUrls) -> None:
    """Gateway Worker 完全无后端凭据：任何一个后端 URL 都拒绝启动。"""
    if urls.transport_mode != "gateway":
        raise ValueError("WORKER_GATEWAY_BACKENDLESS 仅允许 Gateway Worker 使用。")
    if urls.database_url or urls.redis_url:
        raise ValueError("Gateway Worker 禁止注入 DATABASE_URL 或 REDIS_URL。")


def validate_direct_worker_backends(urls: BackendUrls) -> None:
    """Direct Worker 的后端边界是非对称的，两侧都必须显式成立。

    Redis 侧：Worker 只能持有 ``WORKER_REDIS_URL`` 指向的独立最小权限 ACL 账号
    （``transport/factory.py`` 已对其缺失 fail-closed），拿到控制面的 ``REDIS_URL``
    等于绕过整套 ACL 设计，因此在此拒绝。
    PostgreSQL 侧：direct 模式的产物平面是 ``PostgresArtifactTransferStore``
    （见 ``app/wiring.py::_create_artifact_transfer_store``），源码包下载与产物
    上传都直连 PG blob 存储，所以 ``DATABASE_URL`` 反而是必需的。
    """
    if urls.transport_mode != "direct":
        raise ValueError("WORKER_DIRECT_SCOPED_REDIS 仅允许 Direct Worker 使用。")
    if urls.redis_url:
        raise ValueError("Direct Worker 禁止注入控制面 REDIS_URL，只能使用 WORKER_REDIS_URL。")
    _require_database_url(urls.database_url)


def validate_control_plane_backends(urls: BackendUrls) -> None:
    """控制面（web_api/master/gateway/migration）两个后端都必须齐备。"""
    _require_database_url(urls.database_url)
    if not urls.redis_url:
        raise ValueError("REDIS_URL 必须设置。")
    validate_redis_url(urls.redis_url)


__all__ = [
    "BackendUrls",
    "validate_control_plane_backends",
    "validate_database_url",
    "validate_direct_worker_backends",
    "validate_gateway_worker_backends",
    "validate_redis_url",
]
