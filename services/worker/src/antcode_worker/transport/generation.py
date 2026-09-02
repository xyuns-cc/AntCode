"""Lease-generation error propagation across transport compatibility APIs."""


class GenerationLostError(RuntimeError):
    """本进程的 Lease generation 已被新代际取代（复审 D2）。engine 捕获后
    必须立即 abort 所有执行，而不是当普通轮询异常退避重试——否则旧代际
    僵尸进程最长可与新代际并行到下一次 ownership 续租 tick
    （``engine._RUN_OWNERSHIP_RENEW_INTERVAL_SECONDS``，当前 60s）。"""


def raise_if_generation_lost(error: BaseException) -> None:
    """Preserve generation loss instead of degrading it to a bool failure."""
    if isinstance(error, GenerationLostError):
        raise error
