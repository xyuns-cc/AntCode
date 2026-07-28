"""Retry configuration request contract."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RetryConfigUpdate(BaseModel):
    """Partial retry configuration update."""

    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_delay: int | None = Field(default=None, ge=10, le=3600)
    strategy: Literal["exponential"] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "RetryConfigUpdate":
        if not self.model_fields_set:
            raise ValueError("至少提供一个重试配置字段")
        null_fields = [name for name in self.model_fields_set if getattr(self, name) is None]
        if null_fields:
            raise ValueError(f"重试配置字段不能为 null: {', '.join(sorted(null_fields))}")
        return self

    def database_changes(self) -> dict[str, object]:
        changes: dict[str, object] = {}
        if "max_retries" in self.model_fields_set:
            assert self.max_retries is not None
            changes["retry_count"] = self.max_retries
        if "retry_delay" in self.model_fields_set:
            assert self.retry_delay is not None
            changes["retry_delay"] = self.retry_delay
        return changes


__all__ = ["RetryConfigUpdate"]
