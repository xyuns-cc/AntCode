import re
from pathlib import Path

from tests.unit.core.compose_support import load_compose

PROD_COMPOSE = Path("infra/docker/docker-compose.prod.yml")
PROXY_CONFIG = Path("infra/docker/nginx.prod.conf")
FRONTEND_DOCKERFILE = Path("web/antcode-frontend/Dockerfile")
EXPECTED_EDGE_XFF_DIRECTIVES = 3
EXPECTED_FRONTEND_XFF_DIRECTIVES = 2
DOCKER_EMBEDDED_DNS = "resolver 127.0.0.11 valid=10s ipv6=off;"
UPSTREAM_BLOCK = re.compile(r"^\s*upstream\s+\S+\s*\{", re.MULTILINE)
PROXY_TARGET = re.compile(r"proxy_pass\s+http://([^;\s]+)")
LOCAL_RESTART_PROBE = "/usr/local/bin/antcode-healthcheck"


def _compose(path: Path = PROD_COMPOSE) -> dict:
    return load_compose(path)


def test_production_worker_installer_and_proxy_chain_fail_closed() -> None:
    services = _compose()["services"]
    environment = services["web-api"]["environment"]

    required = {
        "API_BASE_URL": "ANTCODE_PUBLIC_API_BASE_URL",
        "GATEWAY_HOST": "ANTCODE_GATEWAY_HOST",
        "GATEWAY_PORT": "ANTCODE_GATEWAY_PUBLIC_PORT",
        "WORKER_INSTALL_SOURCE_URL": "ANTCODE_WORKER_INSTALL_SOURCE_URL",
        "WORKER_INSTALL_SOURCE_REF": "ANTCODE_WORKER_INSTALL_SOURCE_REF",
        "WORKER_INSTALL_UV_VERSION": "ANTCODE_WORKER_INSTALL_UV_VERSION",
    }
    for setting, source in required.items():
        assert environment[setting].startswith(f"${{{source}:?")
    assert environment["WORKER_INSTALL_GATEWAY_TLS"] == "true"
    assert environment["WORKER_INSTALL_CONFIG_REQUIRED"] == "true"
    assert environment["ANTCODE_TRUSTED_PROXIES"].startswith("${ANTCODE_FRONTEND_CONTROL_IP:?")
    frontend = services["frontend"]
    assert frontend["networks"]["antcode-control"]["ipv4_address"].startswith("${ANTCODE_FRONTEND_CONTROL_IP:?")
    assert frontend["environment"]["TRUSTED_EDGE_PROXY_IP"].startswith("${ANTCODE_REVERSE_PROXY_EDGE_IP:?")


def test_reverse_proxy_configuration_sanitizes_forwarded_chain() -> None:
    reverse_proxy = _compose()["services"]["reverse-proxy"]
    config = PROXY_CONFIG.read_text(encoding="utf-8")
    frontend_config = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    assert "./nginx.prod.conf:/etc/nginx/nginx.conf:ro" in reverse_proxy["volumes"]
    assert reverse_proxy["user"] == "101:101"
    assert reverse_proxy["cap_drop"] == ["ALL"]
    assert "cap_add" not in reverse_proxy
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" not in config
    assert config.count("proxy_set_header X-Forwarded-For $remote_addr;") == EXPECTED_EDGE_XFF_DIRECTIVES
    assert "set_real_ip_from ${TRUSTED_EDGE_PROXY_IP};" in frontend_config
    assert "real_ip_header X-Forwarded-For;" in frontend_config
    assert frontend_config.count("proxy_set_header X-Forwarded-For $remote_addr;") == EXPECTED_FRONTEND_XFF_DIRECTIVES
    assert "healthz" in " ".join(reverse_proxy["healthcheck"]["test"])


def test_edge_proxy_deduplicates_the_security_headers_it_re_adds() -> None:
    """三层各设一遍安全头，nginx add_header 只追加不覆盖，客户端会收到三份。

    RFC 7034 §2.1 明确多值 X-Frame-Options 无效、浏览器可以直接忽略整条头，
    所以最外层反代必须先隐藏上游同名头再添加唯一一份（真机 curl 实测出现三份）。
    Content-Security-Policy 不在此列：反代不提供 CSP，隐藏等于抹掉 frontend 的页面 CSP。
    """
    config = PROXY_CONFIG.read_text(encoding="utf-8")

    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert f"proxy_hide_header {header};" in config
        assert config.count(f"add_header {header} ") == 1
    assert "proxy_hide_header Content-Security-Policy;" not in config


def test_proxy_chain_re_resolves_upstreams_on_every_request() -> None:
    """字面主机名的 proxy_pass 只在装载配置时解析一次，之后永久缓存那个 IP。

    deploy-production.sh 会 stop 再 up 控制面，Docker IPAM 按 lowest-free-first
    重新分配地址；frontend / reverse-proxy 不在重启序列里（镜像 digest 不变就不
    重建），于是它们会一直把请求打到已经属于别的容器的旧地址，且永不自愈
    （真机实测三个容器地址互换后 /api 恒定 502）。变量形式的 proxy_pass 才会
    每次请求走 Docker 内嵌 DNS 重新解析。
    """
    edge = PROXY_CONFIG.read_text(encoding="utf-8")
    frontend = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    for label, config in (("edge", edge), ("frontend", frontend)):
        assert DOCKER_EMBEDDED_DNS in config, label
        targets = PROXY_TARGET.findall(config)
        assert targets, label
        assert all(target.startswith("$") for target in targets), (label, targets)
    assert UPSTREAM_BLOCK.search(edge) is None
    assert "set $antcode_frontend frontend:8080;" in edge
    assert "set $antcode_web_api ${WEB_API_UPSTREAM};" in frontend


def test_edge_health_checks_probe_the_real_upstream_chain() -> None:
    """只探本地静态资源的健康检查会在 /api 全挂时照样报 healthy。

    frontend 的 `index.html` 与 reverse-proxy 的 `return 204` 都不碰上游，
    `up -d --wait` 因此会为一个 /api 全 502 的栈宣告部署成功。上游探针必须存在，
    但**不能**进包装器的重启判据：那会把后端故障放大成边缘重启循环。
    """
    services = _compose()["services"]
    edge = PROXY_CONFIG.read_text(encoding="utf-8")
    probes = {
        "frontend": (" ".join(services["frontend"]["healthcheck"]["test"]), "/api/v1/health"),
        "reverse-proxy": (" ".join(services["reverse-proxy"]["healthcheck"]["test"]), "/healthz/upstream"),
    }

    for name, (probe, upstream_path) in probes.items():
        assert upstream_path in probe, name
        assert probe.count(LOCAL_RESTART_PROBE) == 1, name
        assert probe.index(LOCAL_RESTART_PROBE) < probe.index(upstream_path), name
    assert "location = /healthz/upstream" in edge
    assert "proxy_pass http://$antcode_frontend/api/v1/health;" in edge
