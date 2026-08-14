from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.domain.models.enums import ProjectType
from antcode_web_api.routes.v1 import project as route


@pytest.mark.asyncio
async def test_update_file_config_blank_json_fields_are_cleared():
    project = SimpleNamespace(id=1, public_id="proj-001", type=ProjectType.FILE)
    updated_project = SimpleNamespace(id=1, public_id="proj-001", type=ProjectType.FILE)

    with (
        patch.object(
            route.project_service,
            "get_project_by_id",
            AsyncMock(return_value=project),
        ),
        patch.object(
            route.project_service,
            "update_file_config",
            AsyncMock(return_value=updated_project),
        ) as update_file_config,
        patch(
            "antcode_web_api.routes.v1.project.create_project_response",
            return_value={},
        ),
        patch(
            "antcode_web_api.routes.v1.project._attach_project_detail_info",
            AsyncMock(return_value=None),
        ),
    ):
        await route.update_file_config(
            "proj-001",
            language="go",
            entry_point=None,
            runtime_config="   ",
            environment_vars="",
            current_user_id=100,
        )

    request = update_file_config.await_args.args[1]
    assert request.language == "go"
    assert request.runtime_config == {}
    assert request.environment_vars == {}
