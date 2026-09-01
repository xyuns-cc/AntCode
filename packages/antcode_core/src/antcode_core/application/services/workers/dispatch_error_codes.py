"""派发失败的结构化错误码。

调用方必须不看文案就能分清"稍后重试"和"我们坏了"：**容量类**失败（挑不出可用节点、
选中的那台心跳已经过期）属于前者，队列写不进去、项目同步失败、未预期异常属于后者。这两
类从前共用一个 500，调用方只能靠中文文案去猜。

形状照抄 ``alert_delivery_status.ChannelSendOutcome``，不另起炉灶：``error_code`` 是
程序唯一可判定的契约，``error`` 只装给人看的原文。**禁止**对 ``error`` 做匹配分支——
仓里已有 ``"NOSCRIPT" in str(exc)`` 这种字符串契约漂成死代码的 P0 前科。
"""

from typing import Final

DISPATCH_EMPTY_BATCH: Final = "DISPATCH_EMPTY_BATCH"
DISPATCH_NO_CAPACITY: Final = "DISPATCH_NO_CAPACITY"
DISPATCH_WORKER_OFFLINE: Final = "DISPATCH_WORKER_OFFLINE"
DISPATCH_SOURCE_SYNC_FAILED: Final = "DISPATCH_SOURCE_SYNC_FAILED"
DISPATCH_QUEUE_WRITE_FAILED: Final = "DISPATCH_QUEUE_WRITE_FAILED"
DISPATCH_UNEXPECTED_ERROR: Final = "DISPATCH_UNEXPECTED_ERROR"

# 容量类：Worker 侧现在接不下，过一会儿可能就接得下。调用方该重试，运维不该被叫醒。
CAPACITY_ERROR_CODES: Final = frozenset({DISPATCH_NO_CAPACITY, DISPATCH_WORKER_OFFLINE})

__all__ = [
    "CAPACITY_ERROR_CODES",
    "DISPATCH_EMPTY_BATCH",
    "DISPATCH_NO_CAPACITY",
    "DISPATCH_QUEUE_WRITE_FAILED",
    "DISPATCH_SOURCE_SYNC_FAILED",
    "DISPATCH_UNEXPECTED_ERROR",
    "DISPATCH_WORKER_OFFLINE",
]
