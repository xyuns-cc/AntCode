"""删除类拒绝的对外表达：单条走 HTTP 状态码，批量走逐项原因。

单条删除把守卫的拒绝原文原样放进 409 detail（"执行 X 仍由在线 Worker Y 持有…"）。
批量入口没有 per-item 状态码可用，过去就把同一批异常压进 ``except Exception``，
只回一串 ``failed_ids``——运维只看到"某几个失败了"，看不到该去取消哪条执行。

因此两者共用下面这一套分类，别处不要再写第二份：

- ``RunSettlementGuardUnavailable``：结算状态不可得，逐项定性无从谈起，整批 503；
- 域内拒绝（``RunSettlementPendingError`` / ``ValueError``）与下游自己挑好文案的
  ``HTTPException``：原文即可读原因，原样带出；
- 其余异常：只进日志，对外给固定文案，不把内部细节写进响应体
  （与单条删除 ``except Exception -> 500 "删除任务失败"`` 同一取舍）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.workers.run_settlement_guard import (
    RunSettlementGuardUnavailable,
    RunSettlementPendingError,
)
from fastapi import HTTPException, status
from loguru import logger

DELETE_ERRORS = (RunSettlementGuardUnavailable, RunSettlementPendingError, ValueError)
UNAVAILABLE_ERRORS = (RunSettlementGuardUnavailable,)
# 消息本身就是给人看的：守卫的域内拒绝，加上下游 handler 自己定好的 HTTP 文案。
READABLE_REJECTIONS = (RunSettlementPendingError, ValueError, HTTPException)

BatchOperation = Callable[[str], Awaitable[Any]]


def deletion_http_exception(exc: Exception) -> HTTPException:
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if isinstance(exc, RunSettlementGuardUnavailable)
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=status_code, detail=str(exc))


def rejection_reason(exc: Exception, unexpected: str) -> str:
    """异常 → 可读原因；未分类异常只给固定文案，细节留在日志里。"""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) if isinstance(exc, READABLE_REJECTIONS) else unexpected


@dataclass(frozen=True)
class BatchFailure:
    """批量结果里的一条失败：id 配一条可读原因。"""

    item_id: str
    reason: str


@dataclass(frozen=True)
class BatchReasons:
    """一类批量操作的固定文案。"""

    action: str  # 日志前缀，如 "删除任务"
    missing: str  # 服务层返回 False（对象不存在 / 无权限）时的原因
    unexpected: str  # 未分类异常对外的原因


@dataclass(frozen=True)
class BatchOutcome:
    success_ids: tuple[str, ...]
    failures: tuple[BatchFailure, ...]

    def fields(self, failed_ids_key: str) -> dict[str, Any]:
        """旧字段（计数 + 失败 id 列表）原样保留，``failures`` 追加逐项原因。

        新增键而非改写旧键：既有调用方读 ``failed_ids`` / ``failed_projects``
        的路径一个字节都不变。
        """
        return {
            "success_count": len(self.success_ids),
            "failed_count": len(self.failures),
            failed_ids_key: [failure.item_id for failure in self.failures],
            "failures": [{"id": failure.item_id, "reason": failure.reason} for failure in self.failures],
        }


async def collect_batch_outcome(
    item_ids: Sequence[str] | Iterable[str],
    operate: BatchOperation,
    reasons: BatchReasons,
) -> BatchOutcome:
    """逐项执行并收集失败原因；结算状态不可得时整批中止。"""
    success: list[str] = []
    failures: list[BatchFailure] = []
    for item_id in item_ids:
        failure = await _run_batch_item(item_id, operate, reasons)
        if failure is None:
            success.append(item_id)
        else:
            failures.append(failure)
    return BatchOutcome(tuple(success), tuple(failures))


async def _run_batch_item(item_id: str, operate: BatchOperation, reasons: BatchReasons) -> BatchFailure | None:
    try:
        succeeded = await operate(item_id)
    except UNAVAILABLE_ERRORS as exc:
        raise deletion_http_exception(exc) from exc
    except Exception as exc:
        logger.warning(f"{reasons.action} {item_id} 失败: {exc}")
        return BatchFailure(item_id, rejection_reason(exc, reasons.unexpected))
    return None if succeeded else BatchFailure(item_id, reasons.missing)


__all__ = [
    "DELETE_ERRORS",
    "READABLE_REJECTIONS",
    "UNAVAILABLE_ERRORS",
    "BatchFailure",
    "BatchOperation",
    "BatchOutcome",
    "BatchReasons",
    "collect_batch_outcome",
    "deletion_http_exception",
    "rejection_reason",
]
