"""spool 模式下"给不了的能力"必须在 validate 阶段挡住。

Rule 子进程的环境白名单不含任何 Redis 凭据、沙箱也不放行网络，所以
resume / proxy / dedup 三个开关在这个模式下都不可能生效。放行等于让用户
在表单上打开了功能、任务照跑、能力却静默缺席。
"""

from __future__ import annotations

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import TaskPayload
from antcode_worker.plugins.rule.plugin import RulePlugin


def _payload(**rule_extra) -> TaskPayload:
    return TaskPayload(
        task_type=TaskType.RULE,
        project_id="project-1",
        kwargs={
            "target_url": "https://example.com/list",
            "extraction_rules": [{"desc": "标题", "type": "css", "expr": "h1::text"}],
            **rule_extra,
        },
    )


def test_valid_rule_passes_without_optional_features() -> None:
    """反向控制组：不开这三个开关的规则必须照常通过。"""
    assert RulePlugin().validate(_payload()) == []


def test_dedup_enabled_is_rejected() -> None:
    errors = RulePlugin().validate(_payload(dedup_config={"enabled": True, "fields": ["标题"]}))

    assert any("dedup_config" in error for error in errors), errors


def test_dedup_disabled_or_absent_is_accepted() -> None:
    """只在 enabled=True 时拦；存量的关闭配置不该把任务卡死。"""
    assert RulePlugin().validate(_payload(dedup_config={"enabled": False, "fields": ["标题"]})) == []
    assert RulePlugin().validate(_payload(dedup_config={})) == []


def test_dedup_config_wrong_type_is_rejected() -> None:
    errors = RulePlugin().validate(_payload(dedup_config="not-an-object"))

    assert any("dedup_config 必须是对象" in error for error in errors), errors


@pytest.mark.parametrize(
    ("rule_extra", "expected"),
    [
        ({"resume_enabled": True}, "resume_enabled"),
        ({"proxy_config": {"enabled": True, "proxy_url": "http://127.0.0.1:8080"}}, "proxy_config"),
    ],
)
def test_sibling_spool_gates_still_reject(rule_extra: dict, expected: str) -> None:
    """这两条原本就该拦，钉住它们免得 dedup 的改动把兄弟分支带歪。"""
    errors = RulePlugin().validate(_payload(**rule_extra))

    assert any(expected in error for error in errors), errors
