"""Redacted response builders for generic system configuration routes."""

from antcode_core.common.security.system_config_secrets import redact_config_value
from antcode_core.domain.schemas.system_config import SystemConfigResponse


def redacted_system_config(config: object) -> SystemConfigResponse:
    response = SystemConfigResponse.model_validate(config)
    return response.model_copy(update={"config_value": redact_config_value(response.config_key, response.config_value)})


__all__ = ["redacted_system_config"]
