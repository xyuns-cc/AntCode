from antcode_worker.domain.models import ExecPlan
from antcode_worker.engine.rule_egress import rule_egress_plan


def test_rule_execution_plan_gets_loopback_egress_proxy() -> None:
    plan = ExecPlan(command="python", plugin_name="rule", env={"ANTCODE_SPIDER_SINK_MODE": "spool"})

    with rule_egress_plan(plan) as secured:
        proxy_url = secured.env["ANTCODE_SPIDER_EGRESS_PROXY"]
        assert proxy_url.startswith("http://127.0.0.1:")

    assert "ANTCODE_SPIDER_EGRESS_PROXY" not in plan.env
