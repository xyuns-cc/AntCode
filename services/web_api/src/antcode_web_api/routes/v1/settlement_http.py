"""运行结算保护异常的 HTTP 映射。"""

from antcode_core.application.services.workers.run_settlement_guard import (
    RunSettlementGuardUnavailable,
    RunSettlementPendingError,
)
from fastapi import HTTPException, status

DELETE_ERRORS = (RunSettlementGuardUnavailable, RunSettlementPendingError, ValueError)
UNAVAILABLE_ERRORS = (RunSettlementGuardUnavailable,)


def deletion_http_exception(exc: Exception) -> HTTPException:
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if isinstance(exc, RunSettlementGuardUnavailable)
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=status_code, detail=str(exc))
