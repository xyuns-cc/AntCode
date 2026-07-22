"""P0-03 回归：Rule 插件默认不放行网络,需 ANTCODE_RULE_ALLOW_NETWORK 显式开启。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P0-03:
`services/worker/src/antcode_worker/plugins/rule/plugin.py` 之前
`sandbox_config={RULE_SANDBOX_ALLOW_NETWORK: True}` 恒真,让 Rule 任务
恒执行不带 --unshare-net 的 bwrap,与宿主共享网络 namespace。结合 SYS_ADMIN
+ --ro-bind / /,Rule 载荷可对宿主网络(含内部服务)自由发起连接,是不可信租户
的横向移动向量。

本测试锁死:
1. 默认(不设 ANTCODE_RULE_ALLOW_NETWORK)时,build_plan 返回的 sandbox_config
   RULE_SANDBOX_ALLOW_NETWORK=False
2. 设为 "1"/"true"/"yes"/"on" 显式开启后为 True
3. 设为其他值(空/0/false/random)时仍为 False
"""

from __future__ import annotations

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, TaskPayload
from antcode_worker.executor.rule_policy import RULE_SANDBOX_ALLOW_NETWORK
from antcode_worker.plugins.rule.plugin import RulePlugin


def _make_context_and_payload(tmp_path):
    context = RunContext(
        run_id="test-rule-network",
        task_id="task-1",
        project_id="proj-1",
        timeout_seconds=60,
        memory_limit_mb=512,
        cpu_limit_seconds=30,
    )
    payload = TaskPayload(
        task_type=TaskType.RULE,
        project_id="proj-1",
        workspace_path=str(tmp_path / "ws"),
        project_cwd=str(tmp_path / "ws"),
        artifact_patterns=[],
        kwargs={
            "target_url": "https://example.com/foo",
            "extraction_rules": [{"selector": "h1", "field": "title"}],
        },
    )
    return context, payload


@pytest.mark.asyncio
async def test_rule_network_disabled_by_default(tmp_path, monkeypatch):
    """P0-03 关键不变量:未设 ANTCODE_RULE_ALLOW_NETWORK 时 network 关闭。"""
    monkeypatch.delenv("ANTCODE_RULE_ALLOW_NETWORK", raising=False)

    plugin = RulePlugin()
    context, payload = _make_context_and_payload(tmp_path)
    plan = await plugin.build_plan(context, payload)

    assert plan.sandbox_config.get(RULE_SANDBOX_ALLOW_NETWORK) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
async def test_rule_network_enabled_by_explicit_truthy(tmp_path, monkeypatch, value):
    """P0-03:显式 truthy 值放行。"""
    monkeypatch.setenv("ANTCODE_RULE_ALLOW_NETWORK", value)

    plugin = RulePlugin()
    context, payload = _make_context_and_payload(tmp_path)
    plan = await plugin.build_plan(context, payload)

    assert plan.sandbox_config.get(RULE_SANDBOX_ALLOW_NETWORK) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "random", "  "])
async def test_rule_network_stays_off_for_non_truthy(tmp_path, monkeypatch, value):
    """P0-03:非明确 truthy 值一律保持关闭,防止误配置。"""
    monkeypatch.setenv("ANTCODE_RULE_ALLOW_NETWORK", value)

    plugin = RulePlugin()
    context, payload = _make_context_and_payload(tmp_path)
    plan = await plugin.build_plan(context, payload)

    assert plan.sandbox_config.get(RULE_SANDBOX_ALLOW_NETWORK) is False


def test_credential_mask_includes_k8s_and_docker_secret_paths():
    """P0-03:credential mask 覆盖 K8s SA token + docker/podman secrets 挂载点。

    这些路径在 K8s Pod 内被自动挂载,如果不 tmpfs 掩掉,用户载荷可通过
    --ro-bind / / 直接读到 ServiceAccount token。测试通过检查候选清单结构。
    """
    # 直接读取源代码看 candidates 里是否包含新增路径
    import inspect

    from antcode_worker.executor.sandbox import BasicSandbox

    source = inspect.getsource(BasicSandbox._credential_mask_dirs)
    assert "/run/secrets" in source
    assert "/var/run/secrets/kubernetes.io" in source
    assert "/etc/kubernetes" in source
    assert "/var/lib/kubelet" in source
