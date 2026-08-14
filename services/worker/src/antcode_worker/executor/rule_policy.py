"""Rule 子进程的可信执行策略。"""

SPIDER_STATS_PATH_ENV = "ANTCODE_SPIDER_STATS_PATH"
RULE_EGRESS_PROXY_ENV = "ANTCODE_SPIDER_EGRESS_PROXY"
RULE_EGRESS_SOCKET_CONFIG = "rule_egress_socket"
RULE_EGRESS_CONNECTION_LIMIT_CONFIG = "rule_egress_max_connections"
RULE_EGRESS_BYTE_BUDGET_CONFIG = "rule_egress_max_bytes"
RULE_EGRESS_DURATION_CONFIG = "rule_egress_max_duration_seconds"
RULE_EGRESS_LOOPBACK_PORT = 17891

RULE_PLUGIN_ENV_VARS = frozenset(
    {
        "ANTCODE_SPIDER_RUN_ID",
        "ANTCODE_SPIDER_PROJECT_ID",
        "ANTCODE_SPIDER_SINK_MODE",
        "ANTCODE_SPIDER_SPOOL_PATH",
        SPIDER_STATS_PATH_ENV,
        RULE_EGRESS_PROXY_ENV,
    }
)

RULE_SANDBOX_ALLOW_NETWORK = "allow_network"


def is_rule_env_allowed(key: str) -> bool:
    """只允许 Rule 所需的非敏感应用环境变量。"""
    return key in RULE_PLUGIN_ENV_VARS


def filter_spider_control_env(env: dict[str, str]) -> dict[str, str]:
    """Remove control variables that must come from the trusted reporter."""
    return {
        key: value for key, value in env.items() if not key.startswith("ANTCODE_SPIDER_") or key in RULE_PLUGIN_ENV_VARS
    }
