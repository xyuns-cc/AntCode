"""healthcheck 的重启语义契约。

被这套断言钉死的两个缺陷（都在真机上咬过）：

1. **readiness 当 liveness 用**。`/health/ready` 回答的是"现在能不能接新活"，
   包含依赖可达性与背压；拿它决定杀不杀进程，等于把"忙"和"Redis 抖了一下"
   判成"进程坏了"。压满的 Worker 因此自杀，多节点级联丢 30%~50% 的 run。
2. **`retries` / `start_period` 是死配置**。`kill` 内联在 test 命令里时，第一次
   失败就已经把 PID 1 杀了，连续失败次数永远攒不到，宽限期也从未生效。

修法是所有会触发重启的探针统一走 `infra/docker/healthcheck.sh`：它只在连续失败
达到 `retries` **且**过了 `start_period` 之后才 `kill -TERM 1`，而"能不能接新活"
留在包装器外面，只决定 health 状态（供 `depends_on` 与 `up --wait`）。
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml

from tests.unit.core.compose_support import load_compose

DOCKER_DIR = Path("infra/docker")
PROD_COMPOSE = DOCKER_DIR / "docker-compose.prod.yml"
WORKER_COMPOSE = DOCKER_DIR / "docker-compose.prod.worker.yml"
LOCAL_BACKUP_COMPOSE = DOCKER_DIR / "docker-compose.prod.local-backup.yml"
DEV_COMPOSE = DOCKER_DIR / "docker-compose.dev.yml"

WRAPPER = "/usr/local/bin/antcode-healthcheck"
WRAPPER_MOUNT = f"./healthcheck.sh:{WRAPPER}:ro"
STATE_TMPFS = "/tmp"
INLINE_KILL = "kill -TERM 1"
WRAPPER_ARGC = 4
READINESS_PATHS = ("/health/ready", "/api/v1/health/ready")
FAIL_ON_STATUS_FLAGS = ("-f", "-fsS", "--fail")
# 会自愈重启的服务必须逐一列出：漏配一个就等于那个容器永不重启（fail-open）。
RESTARTING_SERVICES = {
    PROD_COMPOSE: ("postgres", "redis", "web-api", "master", "gateway", "worker", "frontend", "reverse-proxy"),
    WORKER_COMPOSE: ("worker",),
    LOCAL_BACKUP_COMPOSE: ("backup-local",),
    DEV_COMPOSE: ("worker",),
}
# 这些服务的 liveness 探针本来就只打本地（nginx 静态页 / 本机中间件 / 本地文件），
# 不含任何跨服务依赖，因此不受 "不许探 readiness" 那条约束。
LOCAL_ONLY_PROBE_SERVICES = frozenset({"postgres", "redis", "frontend", "reverse-proxy", "backup-local"})


def _services(path: Path) -> dict[str, Any]:
    return load_compose(path)["services"]


def _probe_command(service: dict[str, Any]) -> str:
    test = service["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL", test
    return test[1]


def _wrapper_call(service: dict[str, Any]) -> list[str]:
    """取出包装器调用的前四段：可执行文件、max_failures、grace、liveness 探针。"""
    tokens = shlex.split(_probe_command(service))
    return tokens[:WRAPPER_ARGC]


def _seconds(duration: Any) -> str:
    """把 compose 的 `45s` / `${VAR:-7200}s` 归一成包装器参数应有的字面量。"""
    text = str(duration)
    assert text.endswith("s"), text
    return text[:-1]


def _compose_documents() -> list[tuple[Path, str]]:
    files = sorted(DOCKER_DIR.glob("docker-compose*.yml")) + [Path("tests/contracts/docker-compose.test.yml")]
    return [(path, path.read_text(encoding="utf-8")) for path in files]


def _all_declared_probes() -> list[tuple[str, str, str]]:
    """所有 compose 文件里声明过的 healthcheck 命令（含 overlay，不做 extends 展开）。"""
    probes = []
    for path, text in _compose_documents():
        for name, service in (yaml.safe_load(text).get("services") or {}).items():
            healthcheck = (service or {}).get("healthcheck")
            if healthcheck:
                probes.append((str(path), name, " ".join(healthcheck["test"])))
    return probes


def test_no_compose_file_kills_pid_one_from_inside_the_probe() -> None:
    """内联 kill 是缺陷本体：它让 retries 与 start_period 同时失效。"""
    offenders = [(path, name) for path, name, probe in _all_declared_probes() if INLINE_KILL in probe]

    assert offenders == [], offenders


def test_every_self_healing_service_runs_the_shared_wrapper() -> None:
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            call = _wrapper_call(services[name])
            assert call[0] == WRAPPER, (path, name, call)
            assert len(call) == WRAPPER_ARGC, (path, name, call)


def test_wrapper_thresholds_equal_the_docker_healthcheck_settings() -> None:
    """包装器的两个数字就是 `retries` 与 `start_period`。

    它们必须逐字相等——不等就又回到"运维看到的容错余量和实际行为对不上"，
    只不过这次是反向骗人。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            healthcheck = services[name]["healthcheck"]
            _, max_failures, grace, _ = _wrapper_call(services[name])
            assert max_failures == str(healthcheck["retries"]), (path, name)
            assert grace == _seconds(healthcheck["start_period"]), (path, name)


def test_wrapper_is_mounted_and_its_state_lives_on_tmpfs() -> None:
    """包装器靠 tmpfs 上的计数文件工作。

    落到可写根文件系统上时，`docker restart` 不会清空它：重启后的容器会读到
    上一代的连续失败计数与 grace 起点，第一次探测失败就直接再杀一次。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            service = services[name]
            assert WRAPPER_MOUNT in service["volumes"], (path, name)
            mounts = [str(entry).split(":", 1)[0] for entry in service["tmpfs"]]
            assert STATE_TMPFS in mounts, (path, name, service["tmpfs"])


def test_self_healing_requires_a_restart_policy_that_actually_restarts() -> None:
    """Compose 不会因为 unhealthy 做任何事，重启完全靠 PID 1 退出 + restart 策略。

    `restart: "no"` 的服务被 kill 掉就是永久死亡，那是把自愈改成了自杀。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            assert services[name]["restart"] == "unless-stopped", (path, name)


def test_restart_decision_never_consumes_the_readiness_verdict() -> None:
    """包装器里那一条探针决定"要不要重启"，因此不能采信就绪结论。

    就绪结论含依赖可达性（Redis / Postgres / 上游 API）与背压信号，用它决定重启
    就是把中间件抖动和满负载放大成整个控制面的重启风暴。允许复用 readiness 端点
    （Gateway 没有独立 live 端点），但只能读响应体里的**组件**字段，必须丢掉
    聚合状态码——所以此时不许带 `--fail`，且必须真的过滤。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            if name in LOCAL_ONLY_PROBE_SERVICES:
                continue
            probe = _wrapper_call(services[name])[-1]
            if not any(readiness in probe for readiness in READINESS_PATHS):
                continue
            assert not any(flag in shlex.split(probe) for flag in FAIL_ON_STATUS_FLAGS), (path, name, probe)
            assert "grep -q" in probe, (path, name, probe)


def test_gateway_liveness_reads_only_the_grpc_component() -> None:
    """Gateway 唯一"重启才能修"的故障是 gRPC server 没在 listen。

    `_readiness_response` 把各组件分列在响应体里，`"grpc":"ok"` 就是那个判据；
    Redis / DB 不可达只让整体转 503，重启 Gateway 修不好，只会把所有 Worker
    一起踢下线。响应体格式由 tests/unit/gateway 的用例钉住。
    """
    probe = _wrapper_call(_services(PROD_COMPOSE)["gateway"])[-1]

    assert '"grpc":"ok"' in probe


def test_readiness_still_gates_health_status_for_the_application_services() -> None:
    """把 readiness 从重启决策里摘出来，不等于不再检查它。

    它仍必须在包装器**之外**跑：容器 health 状态要如实反映"能不能接新活"，
    否则 `depends_on: service_healthy` 与 `up --wait` 会为一个连不上 DB 的
    控制面宣告部署成功。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            if name in LOCAL_ONLY_PROBE_SERVICES:
                continue
            command = _probe_command(services[name])
            tail = command.split("&&", 1)[1] if "&&" in command else ""
            assert any(readiness in tail for readiness in READINESS_PATHS), (path, name, command)


def test_probe_timeouts_fit_inside_the_docker_healthcheck_timeout() -> None:
    """探针必须自带上限且总和小于 `timeout`。

    否则 Docker 会在 timeout 到点时杀掉整个 test 命令，包装器来不及做重启决策，
    "卡死"这个最需要自愈的场景反而永远等不到重启。
    """
    for path, names in RESTARTING_SERVICES.items():
        services = _services(path)
        for name in names:
            healthcheck = services[name]["healthcheck"]
            command = _probe_command(services[name])
            if "curl" not in command and "wget" not in command:
                continue
            limits = [int(token) for token in _probe_limits(command)]
            assert limits, (path, name, command)
            assert sum(limits) < int(_seconds(healthcheck["timeout"])), (path, name, limits)


def _probe_limits(command: str) -> list[str]:
    tokens = shlex.split(command.replace("&&", " "))
    nested = [inner for token in tokens for inner in shlex.split(token)] if tokens else []
    limits: list[str] = []
    for index, token in enumerate(nested):
        if token in ("--max-time", "-T"):
            limits.append(nested[index + 1])
    return limits


def test_dev_and_prod_worker_share_the_same_restart_semantics() -> None:
    """dev 是开发者观察到的行为基准，语义跑偏会让生产缺陷在开发环境隐身。"""
    dev_probe = _wrapper_call(_services(DEV_COMPOSE)["worker"])[-1]
    prod_probe = _wrapper_call(_services(WORKER_COMPOSE)["worker"])[-1]

    assert dev_probe == prod_probe


def test_worker_liveness_probe_targets_the_dedicated_live_endpoint() -> None:
    """Worker 早就有 /health/live 与 /health/ready 两个端点，healthcheck 却一直
    只探后者——端点用错本身就是缺陷，不只是阈值问题。"""
    probe = _wrapper_call(_services(WORKER_COMPOSE)["worker"])[-1]

    assert "/health/live" in probe


def test_master_id_is_not_injected_anywhere() -> None:
    """MASTER_ID 全仓无人读取，实例身份来自 Redis 锁 token + fencing token。

    留着一个"看起来在配置什么、实际什么都不做"的变量，只会让运维以为改它有用。
    """
    sources = [*_compose_documents(), (Path(".env.example"), (DOCKER_DIR / ".env.example").read_text(encoding="utf-8"))]
    offenders = [str(path) for path, text in sources if "MASTER_ID" in text]

    assert offenders == [], offenders


def test_compose_documents_stay_parseable() -> None:
    """折叠标量里塞了 shell 引号，语法错会在部署时才炸，这里先兜住。"""
    for path, text in _compose_documents():
        assert yaml.safe_load(text)["services"], path
