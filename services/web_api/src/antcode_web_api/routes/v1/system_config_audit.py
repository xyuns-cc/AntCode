"""Audit metadata for committed system configuration changes."""

from antcode_core.application.services.audit import audit_service
from antcode_core.domain.models.audit_log import AuditAction

from antcode_web_api.committed_audit import record_committed_audit


async def audit_config_change(
    current_admin,
    *,
    operation: str,
    config_keys: list[str],
    description: str,
    updated_count: int | None = None,
) -> None:
    """Record key names only because configuration values may be credentials."""
    details: dict[str, object] = {"config_keys": sorted(config_keys)}
    if updated_count is not None:
        details["updated_count"] = updated_count
    await record_committed_audit(
        operation,
        lambda: audit_service.log(
            action=AuditAction.CONFIG_UPDATE,
            resource_type="system_config",
            resource_name=operation,
            username=current_admin.username,
            user_id=current_admin.user_id,
            description=description,
            new_value=details,
        ),
    )


__all__ = ["audit_config_change"]
