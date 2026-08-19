"""告警投递状态与结构化错误码。

单独成模块的原因：alert_manager / alert_service / test_delivery 三处都要表达
"这条告警为什么没送出去"，散成字面量后调用方只能去匹配中文文案，契约一改就漂。
"""

from types import MappingProxyType
from typing import Final

STATUS_QUEUED: Final = "queued"
STATUS_RATE_LIMITED: Final = "rate_limited"
STATUS_NO_CHANNELS: Final = "no_channels"
STATUS_NOT_READY: Final = "not_ready"
STATUS_SHUTTING_DOWN: Final = "shutting_down"
STATUS_ENQUEUE_FAILED: Final = "enqueue_failed"

ERROR_NO_CHANNELS: Final = "ALERT_NO_CHANNELS"
ERROR_CHANNEL_DISABLED: Final = "ALERT_CHANNEL_DISABLED"
ERROR_SEND_FAILED: Final = "ALERT_SEND_FAILED"
ERROR_SEND_TIMEOUT: Final = "ALERT_SEND_TIMEOUT"

# 未投递状态 → 结构化错误码。告警是最不该静默失败的东西：任何没送出去的
# 结果都必须带码返回，让调用方与运维不依赖日志文案就能判定。
UNDELIVERED_ERROR_CODES: Final = MappingProxyType(
    {
        STATUS_NO_CHANNELS: ERROR_NO_CHANNELS,
        STATUS_NOT_READY: "ALERT_DISPATCHER_NOT_READY",
        STATUS_SHUTTING_DOWN: "ALERT_MANAGER_SHUTTING_DOWN",
        STATUS_RATE_LIMITED: "ALERT_RATE_LIMITED",
        STATUS_ENQUEUE_FAILED: "ALERT_ENQUEUE_FAILED",
    }
)


def undelivered(status: str) -> dict[str, str]:
    """构造未投递结果。未登记的状态直接 KeyError，防止悄悄多出一个无码分支。"""
    return {"status": status, "error_code": UNDELIVERED_ERROR_CODES[status]}


def delivered() -> dict[str, str]:
    return {"status": STATUS_QUEUED}


__all__ = [
    "ERROR_CHANNEL_DISABLED",
    "ERROR_NO_CHANNELS",
    "ERROR_SEND_FAILED",
    "ERROR_SEND_TIMEOUT",
    "STATUS_ENQUEUE_FAILED",
    "STATUS_NO_CHANNELS",
    "STATUS_NOT_READY",
    "STATUS_QUEUED",
    "STATUS_RATE_LIMITED",
    "STATUS_SHUTTING_DOWN",
    "UNDELIVERED_ERROR_CODES",
    "delivered",
    "undelivered",
]
