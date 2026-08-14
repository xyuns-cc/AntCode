"""生产 Compose 的部署拓扑契约：pin 的网络地址与只读容器的可写面。

这两类缺陷都不会在 dev 栈暴露（dev 既不 pin IP 也不 read_only），只有真正按生产
画像起栈才会撞上，且都是"首次部署 100% 失败"级别，所以在这里锁死。
"""

import re
from ipaddress import ip_address, ip_network
from pathlib import Path, PurePosixPath

from scripts.release_e2e_environment import NETWORK_LAYOUT
from tests.unit.core.compose_support import load_compose

PROD_COMPOSE = Path("infra/docker/docker-compose.prod.yml")
WORKER_DOCKERFILE = Path("infra/docker/Dockerfile.worker")
DOCKER_ENV_EXAMPLE = Path("infra/docker/.env.example")
NGINX_UID = 101
PINNED_ADDRESSES = {
    "antcode-control": ("ANTCODE_CONTROL_DYNAMIC_RANGE", "ANTCODE_FRONTEND_CONTROL_IP"),
    "antcode-edge": ("ANTCODE_EDGE_DYNAMIC_RANGE", "ANTCODE_REVERSE_PROXY_EDGE_IP"),
}
NGINX_WRITABLE_PATHS = {
    "frontend": ("/var/cache/nginx", "/var/run", "/etc/nginx/conf.d"),
    "reverse-proxy": ("/var/cache/nginx", "/var/run"),
}
# executor/sandbox_provider.py 用 config.data_dir 当 sandbox 的 data_root。
WORKER_DATA_ROOT = PurePosixPath("/app/data/worker")
TOOLCHAIN_INSTALL_ENVS = ("UV_PYTHON_INSTALL_DIR", "MISE_DATA_DIR", "MISE_STATE_DIR")
MOUNT_TARGET = re.compile(r":(/[^:]+)")


def _compose() -> dict:
    return load_compose(PROD_COMPOSE)


def test_pinned_network_addresses_sit_outside_the_dynamic_ipam_pool() -> None:
    """Docker 动态 IPAM 从网段低位起分配，pin 的地址落在池内会被先起的容器抢走。"""
    networks = _compose()["networks"]

    for network, (range_variable, address_variable) in PINNED_ADDRESSES.items():
        ipam = networks[network]["ipam"]["config"][0]
        assert ipam["ip_range"].startswith(f"${{{range_variable}:?")
        subnet = ip_network(NETWORK_LAYOUT[range_variable.replace("DYNAMIC_RANGE", "SUBNET")])
        dynamic = ip_network(NETWORK_LAYOUT[range_variable])
        pinned = ip_address(NETWORK_LAYOUT[address_variable])
        assert dynamic.subnet_of(subnet)
        assert pinned in subnet
        assert pinned not in dynamic


def test_read_only_nginx_containers_own_their_tmpfs_scratch_space() -> None:
    """tmpfs 默认按 root:root 挂载；不带 uid/gid，以 101 运行的 nginx 一行都写不进去。"""
    services = _compose()["services"]

    for name, required_paths in NGINX_WRITABLE_PATHS.items():
        service = services[name]
        assert service["read_only"] is True
        mounts = {entry.split(":", 1)[0]: entry for entry in service["tmpfs"]}
        for path in required_paths:
            options = mounts[path].split(":", 1)[1]
            assert f"uid={NGINX_UID}" in options, f"{name}:{path}"
            assert f"gid={NGINX_UID}" in options, f"{name}:{path}"


def _toolchain_install_roots() -> dict[str, PurePosixPath]:
    dockerfile = WORKER_DOCKERFILE.read_text(encoding="utf-8")
    roots = {}
    for name in TOOLCHAIN_INSTALL_ENVS:
        declaration = re.search(rf"^\s*(?:ENV\s+)?{name}=(\S+)", dockerfile, re.MULTILINE)
        assert declaration is not None, f"Dockerfile.worker 未声明 {name}"
        roots[name] = PurePosixPath(declaration.group(1))
    return roots


def _writable_roots(service: dict) -> tuple[PurePosixPath, ...]:
    tmpfs = [str(entry).split(":", 1)[0] for entry in service.get("tmpfs", [])]
    mounts = [target for entry in service.get("volumes", []) for target in MOUNT_TARGET.findall(str(entry))]
    return tuple(PurePosixPath(path) for path in (*tmpfs, *mounts))


def test_worker_language_toolchain_roots_are_writable_and_sandbox_trusted() -> None:
    """read_only 的 Worker 必须把 uv/mise 的安装根指到可写卷，且避开 sandbox 的 data_root。

    默认根在 $HOME/.local/share 下，只读根文件系统里除镜像预装的那个解释器外，任何
    python_version 都会以 "os error 30（Read-only file system）" 失败；tmpfs 只盖了
    /tmp 与 ~/.cache。同时安装根不能落进 config.data_dir——sandbox_executables.py
    把该目录内的可执行文件一律当作跨租户数据拒绝挂载。
    """
    worker = _compose()["services"]["worker"]
    writable = _writable_roots(worker)

    assert worker["read_only"] is True
    for name, root in _toolchain_install_roots().items():
        assert any(root.is_relative_to(mount) for mount in writable), f"{name}={root} 落在只读根文件系统上"
        assert not root.is_relative_to(WORKER_DATA_ROOT), f"{name}={root} 会被 sandbox 当作跨租户数据拒绝"


def _declared_example_names() -> set[str]:
    return set(re.findall(r"^#?\s*([A-Z0-9_]+)=", DOCKER_ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE))


def _example_network(variable: str) -> str:
    text = DOCKER_ENV_EXAMPLE.read_text(encoding="utf-8")
    value = re.search(rf"^#?\s*{variable}=(\S+)\s*$", text, re.MULTILINE)
    assert value is not None, f"infra/docker/.env.example 未给出 {variable}"
    return value.group(1)


def _network_members(network: str) -> int:
    services = _compose()["services"].values()
    return sum(1 for service in services if network in (service.get("networks") or ()))


def test_every_required_compose_variable_is_documented_in_the_example() -> None:
    """`${VAR:?}` 缺一个，`compose config` 就直接拒绝解析，整套部署命令全部不可用。"""
    declared = _declared_example_names()
    required = {
        name
        for path in sorted(PROD_COMPOSE.parent.glob("docker-compose.prod*.yml"))
        for name in re.findall(r"\$\{([A-Z0-9_]+):\?", path.read_text(encoding="utf-8"))
    }

    assert required - declared == set()


def test_example_network_layout_is_consistent_and_large_enough() -> None:
    """示例网段必须容得下网上的全部容器，包括 `compose run --rm` 的临时容器。"""
    for network, (range_variable, address_variable) in PINNED_ADDRESSES.items():
        subnet = ip_network(_example_network(range_variable.replace("DYNAMIC_RANGE", "SUBNET")))
        dynamic = ip_network(_example_network(range_variable))
        pinned = ip_address(_example_network(address_variable))

        assert dynamic.subnet_of(subnet)
        assert pinned in subnet
        assert pinned not in dynamic
        assert dynamic.num_addresses - 2 >= _network_members(network) - 1, network


def test_co_located_worker_targets_the_in_network_gateway_endpoint() -> None:
    """同机 Worker 覆盖 Gateway 主机名时必须连端口一起覆盖。

    基线 WORKER_GATEWAY_PORT 是宿主发布端口 ANTCODE_GATEWAY_PUBLIC_PORT，而容器内
    gateway 恒定监听 GRPC_PORT=50051；只改主机名会让同机 Worker 敲一个网络内没有
    监听的端口。TLS 保持开启，因此 Gateway 证书必须含 DNS:gateway
    （docs/release-runbook.md 第 5.4 节）。
    """
    worker = _compose()["services"]["worker"]
    environment = worker["environment"]

    assert worker["profiles"] == ["co-located-worker"]
    assert environment["WORKER_GATEWAY_HOST"] == "gateway"
    assert environment["WORKER_GATEWAY_PORT"] == "50051"
    assert environment["WORKER_GATEWAY_TLS"] == "true"
    assert "DNS:gateway" in Path("docs/release-runbook.md").read_text(encoding="utf-8")
