"""Git 凭证模型。"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id


class GitCredential(BaseModel):
    """项目级 Git HTTPS 凭证。"""

    public_id = fields.CharField(
        max_length=32,
        unique=True,
        default=generate_public_id,
        db_index=True,
    )
    name = fields.CharField(max_length=100)
    auth_type = fields.CharField(max_length=20)
    username = fields.CharField(max_length=255, null=True)
    secret_encrypted = fields.TextField()
    host_scope = fields.CharField(max_length=255)
    owner_user_id = fields.BigIntField(db_index=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "git_credentials"
        indexes = [
            ("public_id",),
            ("owner_user_id",),
            ("owner_user_id", "host_scope"),
            ("owner_user_id", "name"),
        ]

    def __str__(self) -> str:
        return self.name


__all__ = ["GitCredential"]
