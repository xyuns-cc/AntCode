import contextlib

import pytest
from antcode_worker.domain.models import ExecPlan
from antcode_worker.engine.rule_egress import rule_egress_plan
from antcode_worker.executor.rule_policy import (
    RULE_EGRESS_BYTE_BUDGET_CONFIG,
    RULE_EGRESS_CONNECTION_LIMIT_CONFIG,
    RULE_EGRESS_DURATION_CONFIG,
    RULE_EGRESS_LOOPBACK_PORT,
    RULE_EGRESS_SOCKET_CONFIG,
)
from antcode_worker.rule_egress_limits import RuleEgressLimits

_DEFAULT_MAX_PROCESSES = 64
_DEFAULT_LIMITS = RuleEgressLimits()
_EXPECTED_RULE_CONNECTIONS = 32


def test_rule_execution_plan_gets_isolated_loopback_bridge(monkeypatch, tmp_path) -> None:
    spool = tmp_path / "spool.jsonl"
    socket_path = tmp_path / "private" / "p.sock"
    plan = ExecPlan(
        command="python",
        plugin_name="rule",
        env={
            "ANTCODE_SPIDER_SINK_MODE": "spool",
            "ANTCODE_SPIDER_SPOOL_PATH": str(spool),
            "ANTCODE_SPIDER_EGRESS_PROXY": "http://attacker.invalid:8080",
        },
        sandbox_config={RULE_EGRESS_SOCKET_CONFIG: "/tmp/attacker.sock"},
    )

    @contextlib.contextmanager
    def fake_proxy(*, budget, max_connections: int, duration_budget):
        assert budget.max_bytes == 1024 * 1024 * 1024
        assert max_connections == _EXPECTED_RULE_CONNECTIONS
        assert duration_budget.max_duration_seconds == plan.timeout_seconds
        yield "http://127.0.0.1:49152"

    @contextlib.contextmanager
    def fake_bridge(proxy_url: str, work_dir: str, *, max_connections: int, max_duration_seconds: int):
        assert proxy_url == "http://127.0.0.1:49152"
        assert work_dir == str(tmp_path)
        assert max_connections == _EXPECTED_RULE_CONNECTIONS
        assert max_duration_seconds == plan.timeout_seconds
        yield str(socket_path)

    monkeypatch.setattr("antcode_worker.engine.rule_egress.restricted_http_proxy", fake_proxy)
    monkeypatch.setattr("antcode_worker.engine.rule_egress.unix_proxy_bridge", fake_bridge)

    with rule_egress_plan(plan, _DEFAULT_LIMITS, default_max_processes=_DEFAULT_MAX_PROCESSES) as secured:
        assert secured.env["ANTCODE_SPIDER_EGRESS_PROXY"] == f"http://127.0.0.1:{RULE_EGRESS_LOOPBACK_PORT}"
        assert secured.sandbox_config[RULE_EGRESS_SOCKET_CONFIG] == str(socket_path)
        assert secured.sandbox_config[RULE_EGRESS_CONNECTION_LIMIT_CONFIG] == _EXPECTED_RULE_CONNECTIONS
        assert secured.sandbox_config[RULE_EGRESS_BYTE_BUDGET_CONFIG] == _DEFAULT_LIMITS.max_bytes
        assert secured.sandbox_config[RULE_EGRESS_DURATION_CONFIG] == plan.timeout_seconds

    assert plan.env["ANTCODE_SPIDER_EGRESS_PROXY"] == "http://attacker.invalid:8080"
    assert plan.sandbox_config[RULE_EGRESS_SOCKET_CONFIG] == "/tmp/attacker.sock"


def test_rule_egress_uses_plan_process_limit(monkeypatch, tmp_path) -> None:
    requested_limit = 7
    limits = RuleEgressLimits(max_connections=5, max_bytes=4096, max_duration_seconds=300)
    observed: list[int] = []
    plan = ExecPlan(
        command="python",
        plugin_name="rule",
        max_processes=requested_limit,
        env={"ANTCODE_SPIDER_SPOOL_PATH": str(tmp_path / "spool.jsonl")},
    )

    @contextlib.contextmanager
    def fake_proxy(*, budget, max_connections: int, duration_budget):
        observed.append(budget.max_bytes)
        observed.append(max_connections)
        observed.append(duration_budget.max_duration_seconds)
        yield "http://127.0.0.1:49152"

    @contextlib.contextmanager
    def fake_bridge(_proxy_url: str, _work_dir: str, *, max_connections: int, max_duration_seconds: int):
        observed.append(max_connections)
        observed.append(max_duration_seconds)
        yield str(tmp_path / "p.sock")

    monkeypatch.setattr("antcode_worker.engine.rule_egress.restricted_http_proxy", fake_proxy)
    monkeypatch.setattr("antcode_worker.engine.rule_egress.unix_proxy_bridge", fake_bridge)

    with rule_egress_plan(plan, limits, default_max_processes=_DEFAULT_MAX_PROCESSES) as secured:
        assert secured.sandbox_config[RULE_EGRESS_CONNECTION_LIMIT_CONFIG] == limits.max_connections

    assert observed == [
        limits.max_bytes,
        limits.max_connections,
        limits.max_duration_seconds,
        limits.max_connections,
        limits.max_duration_seconds,
    ]


def test_rule_egress_rejects_missing_resource_limit(tmp_path) -> None:
    plan = ExecPlan(
        command="python",
        plugin_name="rule",
        env={"ANTCODE_SPIDER_SPOOL_PATH": str(tmp_path / "spool.jsonl")},
    )

    with pytest.raises(RuntimeError, match="连接上限"):
        with rule_egress_plan(plan, _DEFAULT_LIMITS, default_max_processes=0):
            pass


@pytest.mark.parametrize(
    "limits",
    [
        RuleEgressLimits(max_connections=1, max_bytes=1, max_duration_seconds=1),
        RuleEgressLimits(max_connections=32, max_bytes=1024, max_duration_seconds=60),
    ],
)
def test_rule_egress_limits_are_explicit_positive_contract(limits) -> None:
    assert limits.max_connections > 0
    assert limits.max_bytes > 0
    assert limits.max_duration_seconds > 0


@pytest.mark.parametrize("field", ["max_connections", "max_bytes", "max_duration_seconds"])
def test_rule_egress_rejects_invalid_operator_budget(field) -> None:
    values = {"max_connections": 1, "max_bytes": 1, "max_duration_seconds": 1, field: 0}

    with pytest.raises(ValueError, match="正整数"):
        RuleEgressLimits(**values)
