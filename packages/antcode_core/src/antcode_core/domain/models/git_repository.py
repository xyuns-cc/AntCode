"""Git repository resource model."""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id


class GitRepository(BaseModel):
    """Git repository accessible only by Web API and Master."""

    public_id = fields.CharField(
        max_length=32,
        unique=True,
        default=generate_public_id,
        db_index=True,
    )
    name = fields.CharField(max_length=255)
    url = fields.CharField(max_length=2000)
    default_ref = fields.CharField(max_length=255, default="main")
    credential_id = fields.CharField(max_length=32, null=True)
    enabled = fields.BooleanField(default=True)
    owner_user_id = fields.BigIntField(db_index=True)
    last_scan_status = fields.CharField(max_length=32, null=True)
    last_scan_error = fields.TextField(null=True)
    last_scan_result = fields.JSONField(null=True)
    last_scanned_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "git_repositories"
        unique_together = (("owner_user_id", "name"),)
        indexes = [
            ("public_id",),
            ("owner_user_id",),
            ("owner_user_id", "enabled"),
            ("url",),
        ]


__all__ = ["GitRepository"]
