"""Worker 权限管理请求模型。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from antcode_core.domain.models.enums import WorkerPermission

MAX_IDENTIFIER_LENGTH = 64
MAX_PERMISSION_NOTE_LENGTH = 2000
Identifier = (
    StrictInt
    | Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_IDENTIFIER_LENGTH),
    ]
)


class WorkerPermissionAssignRequest(BaseModel):
    """给单个用户分配 Worker 权限。"""

    user_id: Identifier
    permission: WorkerPermission = WorkerPermission.USE
    note: str | None = Field(default=None, max_length=MAX_PERMISSION_NOTE_LENGTH)

    model_config = ConfigDict(extra="forbid")


class WorkerPermissionBatchAssignRequest(BaseModel):
    """给单个用户批量分配 Worker 权限。"""

    user_id: Identifier
    worker_ids: list[Identifier] = Field(min_length=1)
    permission: WorkerPermission = WorkerPermission.USE

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "Identifier",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_PERMISSION_NOTE_LENGTH",
    "WorkerPermissionAssignRequest",
    "WorkerPermissionBatchAssignRequest",
]
