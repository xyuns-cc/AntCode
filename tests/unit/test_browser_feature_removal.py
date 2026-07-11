from datetime import datetime
from types import SimpleNamespace

import pytest
from antcode_core.application.services.projects.project_service import project_service
from antcode_core.domain.schemas.project import (
    ProjectRuleCreateRequest,
    ProjectRuleUpdateRequest,
)
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from antcode_core.domain.schemas.worker import WorkerCapabilities, WorkerResponse
from pydantic import ValidationError


def _build_rule_request(**overrides):
    payload = {
        "name": "Rule Demo",
        "type": "rule",
        "runtime_scope": "shared",
        "python_version": "3.11",
        "worker_id": "worker-001",
        "target_url": "https://example.com",
        "extraction_rules": [{"desc": "title", "type": "css", "expr": "h1"}],
    }
    payload.update(overrides)
    return payload


def _assert_validation_error(exc: ValidationError, loc: tuple[str, ...], err_type: str) -> None:
    assert any(tuple(error["loc"]) == loc and error["type"] == err_type for error in exc.errors())


def test_rule_create_rejects_browser_engine():
    with pytest.raises(ValidationError) as exc_info:
        ProjectRuleCreateRequest(**_build_rule_request(engine="browser"))
    _assert_validation_error(exc_info.value, ("engine",), "enum")


def test_rule_create_rejects_browser_config():
    with pytest.raises(ValidationError) as exc_info:
        ProjectRuleCreateRequest(**_build_rule_request(browser_config={"headless": True}))
    _assert_validation_error(exc_info.value, ("browser_config",), "extra_forbidden")


def test_rule_update_rejects_removed_browser_fields():
    with pytest.raises(ValidationError) as exc_info:
        ProjectRuleUpdateRequest(engine="browser")
    _assert_validation_error(exc_info.value, ("engine",), "enum")

    with pytest.raises(ValidationError) as exc_info:
        ProjectRuleUpdateRequest(browser_config={"headless": True})
    _assert_validation_error(exc_info.value, ("browser_config",), "extra_forbidden")


def test_rule_requests_accept_new_rule_fields():
    create_request = ProjectRuleCreateRequest(
        **_build_rule_request(
            retry_count=2,
            timeout=15,
            dont_filter=True,
            data_schema='{"fields":["title"]}',
            proxy_config='{"enabled":true,"proxy_url":"http://proxy.example.com:8080"}',
            anti_spider='{"enabled":true,"random_delay":true}',
            task_config='{"worker_id":"worker-001"}',
        )
    )
    assert create_request.retry_count == 2
    assert create_request.timeout == 15
    assert create_request.dont_filter is True
    assert create_request.proxy_config == {
        "enabled": True,
        "proxy_url": "http://proxy.example.com:8080",
    }

    update_request = ProjectRuleUpdateRequest(
        engine="curl_cffi",
        url_pattern="https://example.com/.*",
        retry_count=1,
        timeout=20,
        data_schema='{"fields":["title"]}',
        proxy_config='{"enabled":true,"proxy_url":"http://proxy.example.com:8080"}',
        anti_spider='{"enabled":false}',
        task_config='{"worker_id":"worker-002"}',
    )
    assert update_request.engine.value == "curl_cffi"
    assert update_request.url_pattern == "https://example.com/.*"
    assert update_request.task_config == {"worker_id": "worker-002"}


@pytest.mark.asyncio
async def test_generate_task_json_prefers_proxy_url():
    rule_detail = SimpleNamespace(
        engine="requests",
        extraction_rules=[{"desc": "title", "type": "css", "expr": "h1"}],
        pagination_config=None,
        proxy_config={
            "enabled": True,
            "proxy_url": "http://proxy.example.com:8080",
            "proxy": "http://legacy-proxy.example.com:8080",
        },
        task_config=None,
        target_url="https://example.com",
        callback_type="list",
        request_method="GET",
        headers=None,
        cookies=None,
        priority=0,
        dont_filter=False,
    )

    task_json = await project_service.generate_task_json(rule_detail)

    assert task_json.meta.proxy == "http://proxy.example.com:8080"


def test_unified_project_update_rejects_removed_browser_fields():
    with pytest.raises(ValidationError) as exc_info:
        UnifiedProjectUpdateRequest(engine="browser")
    _assert_validation_error(exc_info.value, ("engine",), "enum")

    with pytest.raises(ValidationError) as exc_info:
        UnifiedProjectUpdateRequest(browser_config={"headless": True})
    _assert_validation_error(exc_info.value, ("browser_config",), "extra_forbidden")


def test_worker_schema_drops_render_capability_fields():
    with pytest.raises(ValidationError) as exc_info:
        WorkerCapabilities(
            drissionpage={"enabled": True},
            curl_cffi={"enabled": True},
        )
    _assert_validation_error(exc_info.value, ("drissionpage",), "extra_forbidden")

    response = WorkerResponse(
        id="worker-001",
        name="Worker-001",
        host="127.0.0.1",
        port=8001,
        status="online",
        capabilities={"curl_cffi": {"enabled": True}},
        lastHeartbeat="",
        createdAt=datetime(2024, 1, 1),
    )
    dumped = response.model_dump()
    assert "hasRenderCapability" not in dumped
    assert "drissionpage" not in dumped["capabilities"]
