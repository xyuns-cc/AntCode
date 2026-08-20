"""告警投递状态与结构化错误码。

单独成模块的原因：alert_manager / alert_service / test_delivery 三处都要表达
"这条告警为什么没送出去"，散成字面量后调用方只能去匹配中文文案，契约一改就漂。

模块内有两层：
- **投递层**（``STATUS_*`` / ``undelivered``）：告警有没有进入发送队列。
- **渠道层**（``ERROR_CHANNEL_*`` / ``ChannelSendOutcome``）：真正打到第三方
  之后为什么没成。两层共用同一套码前缀，不另起炉灶。
"""

from dataclasses import dataclass
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


# 渠道层失败码。渠道过去只回 bool，第三方给的真实原因（钉钉的
# ``token is not exist``）止步于一行日志，API/UI 只能显示"发送失败"。
ERROR_CHANNEL_NO_TARGET: Final = "ALERT_CHANNEL_NO_TARGET"
ERROR_CHANNEL_LEVEL_FILTERED: Final = "ALERT_CHANNEL_LEVEL_FILTERED"
ERROR_CHANNEL_URL_REJECTED: Final = "ALERT_CHANNEL_URL_REJECTED"
ERROR_CHANNEL_HTTP_STATUS: Final = "ALERT_CHANNEL_HTTP_STATUS"
ERROR_CHANNEL_BAD_RESPONSE: Final = "ALERT_CHANNEL_BAD_RESPONSE"
ERROR_CHANNEL_REJECTED: Final = "ALERT_CHANNEL_REJECTED"
ERROR_CHANNEL_TIMEOUT: Final = "ALERT_CHANNEL_TIMEOUT"
ERROR_CHANNEL_NETWORK: Final = "ALERT_CHANNEL_NETWORK"
ERROR_CHANNEL_SMTP_AUTH: Final = "ALERT_CHANNEL_SMTP_AUTH"
ERROR_CHANNEL_SMTP: Final = "ALERT_CHANNEL_SMTP"
ERROR_CHANNEL_MISSING: Final = "ALERT_CHANNEL_MISSING"
ERROR_CHANNEL_UNEXPECTED: Final = "ALERT_CHANNEL_UNEXPECTED"
ERROR_CHANNEL_MIXED: Final = "ALERT_CHANNEL_MIXED_FAILURES"

# 明确表达"这次没拿到原因"。不许在拿不到原文时编一个或退回泛化文案，
# 否则运维分不清"渠道没说"和"我们没传"。
REASON_UNAVAILABLE: Final = "未获取到具体原因"


@dataclass(frozen=True)
class ChannelSendOutcome:
    """单个渠道的发送结果。

    两条线严格分开：``error_code`` 是程序唯一可判定的契约；``detail`` 装第三方
    原文/异常文本，只允许出现在给人看的文案里。**禁止**对 ``detail`` 做任何
    匹配分支——仓里已有 ``"NOSCRIPT" in str(exc)`` 这种字符串契约漂成死代码的
    P0 前科。
    """

    ok: bool
    error_code: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        # 无码的失败等于退回"发送失败"那句废话，构造期就必须拦住。
        if not self.ok and not self.error_code:
            raise ValueError("失败结果必须带结构化 error_code")

    @property
    def failure_code(self) -> str:
        """失败码。成功结果没有失败码，直接炸而不是回一个假值糊过去。"""
        if self.ok or self.error_code is None:
            raise ValueError("发送成功的结果没有失败码")
        return self.error_code

    def describe(self) -> str:
        """渲染一行人读原因：``码 (原文)``。成功的结果没有原因可描述，直接炸。"""
        return f"{self.failure_code} ({self.detail or REASON_UNAVAILABLE})"


def channel_sent() -> ChannelSendOutcome:
    return ChannelSendOutcome(ok=True)


def channel_failed(error_code: str, *, detail: str | None = None) -> ChannelSendOutcome:
    return ChannelSendOutcome(ok=False, error_code=error_code, detail=detail)


def merge_channel_outcomes(named_outcomes) -> ChannelSendOutcome:
    """合并同一渠道多个目标（多 Webhook / 多收件人）的结果。

    任一成功即整体成功（沿用原 ``any(...)`` 语义）；全失败时逐目标保留码与
    原文。码一致就沿用该码，不一致降级为 MIXED —— 一个聚合码无法同时精确
    表达多种失败，但每个目标的码都还在 ``detail`` 里，不会丢。
    """
    failures: list[tuple[str, ChannelSendOutcome]] = []
    for name, outcome in named_outcomes:
        resolved = _as_outcome(outcome)
        if resolved.ok:
            return channel_sent()
        failures.append((name, resolved))

    if not failures:
        return channel_failed(ERROR_CHANNEL_NO_TARGET, detail="没有可发送的目标")
    codes = {outcome.failure_code for _name, outcome in failures}
    uniform = len(codes) == 1
    return channel_failed(
        next(iter(codes)) if uniform else ERROR_CHANNEL_MIXED,
        detail=_merged_detail(failures, uniform=uniform),
    )


def _merged_detail(failures: list[tuple[str, ChannelSendOutcome]], *, uniform: bool) -> str:
    if uniform:
        return "; ".join(f"{name}: {outcome.detail or REASON_UNAVAILABLE}" for name, outcome in failures)
    return "; ".join(f"{name}: {outcome.describe()}" for name, outcome in failures)


def _as_outcome(outcome) -> ChannelSendOutcome:
    """``asyncio.gather(return_exceptions=True)`` 会把异常混进结果里。"""
    if isinstance(outcome, ChannelSendOutcome):
        return outcome
    return channel_failed(ERROR_CHANNEL_UNEXPECTED, detail=str(outcome))


__all__ = [
    "ERROR_CHANNEL_BAD_RESPONSE",
    "ERROR_CHANNEL_DISABLED",
    "ERROR_CHANNEL_HTTP_STATUS",
    "ERROR_CHANNEL_LEVEL_FILTERED",
    "ERROR_CHANNEL_MISSING",
    "ERROR_CHANNEL_MIXED",
    "ERROR_CHANNEL_NETWORK",
    "ERROR_CHANNEL_NO_TARGET",
    "ERROR_CHANNEL_REJECTED",
    "ERROR_CHANNEL_SMTP",
    "ERROR_CHANNEL_SMTP_AUTH",
    "ERROR_CHANNEL_TIMEOUT",
    "ERROR_CHANNEL_UNEXPECTED",
    "ERROR_CHANNEL_URL_REJECTED",
    "ERROR_NO_CHANNELS",
    "ERROR_SEND_FAILED",
    "ERROR_SEND_TIMEOUT",
    "REASON_UNAVAILABLE",
    "STATUS_ENQUEUE_FAILED",
    "STATUS_NO_CHANNELS",
    "STATUS_NOT_READY",
    "STATUS_QUEUED",
    "STATUS_RATE_LIMITED",
    "STATUS_SHUTTING_DOWN",
    "UNDELIVERED_ERROR_CODES",
    "ChannelSendOutcome",
    "channel_failed",
    "channel_sent",
    "delivered",
    "merge_channel_outcomes",
    "undelivered",
]
