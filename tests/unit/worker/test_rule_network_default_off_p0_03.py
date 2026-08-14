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
2. 旧的 "1"/"true"/"yes"/"on" 共享网络开关被显式拒绝
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
async def test_unsafe_rule_network_override_is_rejected(tmp_path, monkeypatch, value):
    """共享 Worker network namespace 会绕过受限代理，必须拒绝。"""
    monkeypatch.setenv("ANTCODE_RULE_ALLOW_NETWORK", value)

    plugin = RulePlugin()
    context, payload = _make_context_and_payload(tmp_path)
    with pytest.raises(RuntimeError, match="已禁用"):
        await plugin.build_plan(context, payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "random", "  "])
async def test_rule_network_stays_off_for_non_truthy(tmp_path, monkeypatch, value):
    """P0-03:非明确 truthy 值一律保持关闭,防止误配置。"""
    monkeypatch.setenv("ANTCODE_RULE_ALLOW_NETWORK", value)

    plugin = RulePlugin()
    context, payload = _make_context_and_payload(tmp_path)
    plan = await plugin.build_plan(context, payload)

    assert plan.sandbox_config.get(RULE_SANDBOX_ALLOW_NETWORK) is False


def test_filesystem_allowlist_never_mentions_secret_mounts():
    """空根白名单不需要枚举凭据路径，未知 Secret mount 默认不可见。"""
    import inspect

    from antcode_worker.executor.sandbox_mounts import sandbox_filesystem_args

    module = inspect.getmodule(sandbox_filesystem_args)
    assert module is not None
    source = inspect.getsource(module)
    assert '"--ro-bind", "/", "/"' not in source
    assert "/run/secrets" not in source
    assert "/etc/kubernetes" not in source
    assert "/var/lib/kubelet" not in source
