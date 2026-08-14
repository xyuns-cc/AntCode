"""Git repository resource model."""

from typing import TYPE_CHECKING

from tortoise import fields

from antcode_core.common.error_message_field import PersistedErrorMessageField
from antcode_core.domain.models.base import BaseModel, generate_public_id


class GitRepository(BaseModel):
    """Git repository accessible only by Web API and Master."""

    public_id = fields.CharField(
        max_length=32,
        unique=True,
        default=generate_public_id,
    )
    name = fields.CharField(max_length=255)
    url = fields.CharField(max_length=2000)
    default_ref = fields.CharField(max_length=255, default="main")
    credential_id = fields.CharField(max_length=32, null=True)
    enabled = fields.BooleanField(default=True)
    owner_user_id = fields.BigIntField(db_index=True)
    last_scan_status = fields.CharField(max_length=32, null=True)
    if TYPE_CHECKING:
        last_scan_error: str | None
    else:
        last_scan_error = PersistedErrorMessageField(null=True)
    last_scan_result: fields.JSONField[list[dict[str, object]] | None] = fields.JSONField(null=True)
    last_scanned_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "git_repositories"
        unique_together = (("owner_user_id", "name"),)
        indexes = [
            ("owner_user_id",),
            ("owner_user_id", "enabled"),
            ("url",),
        ]


__all__ = ["GitRepository"]
