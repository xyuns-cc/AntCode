from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.domain.models.enums import ProjectType
from antcode_web_api.routes.v1 import project as project_routes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("project_type", "relation_name", "response_field"),
    [
        (ProjectType.FILE, "get_project_file_detail", "file_info"),
        (ProjectType.CODE, "get_project_code_detail", "code_info"),
    ],
)
async def test_project_detail_returns_persisted_runtime_configuration(
    monkeypatch,
    *,
    project_type,
    relation_name,
    response_field,
) -> None:
    detail = SimpleNamespace(
        language="python",
        entry_point="main.py",
        documentation="docs",
        runtime_config={"pythonpath": ["src"]},
        environment_vars={"API_BASE": "https://example.com"},
    )
    monkeypatch.setattr(
        project_routes.relation_service,
        relation_name,
        AsyncMock(return_value=detail),
    )
    monkeypatch.setattr(
        project_routes.project_source_service,
        "get_response",
        AsyncMock(return_value=SimpleNamespace()),
    )
    monkeypatch.setattr(project_routes, "_source_response_fields", lambda _source: {})
    response = SimpleNamespace(file_info=None, code_info=None, rule_info=None)

    await project_routes._attach_project_detail_info(
        response,
        SimpleNamespace(id=1, type=project_type),
    )

    config = getattr(response, response_field)
    assert config.runtime_config == {"pythonpath": ["src"]}
    assert config.environment_vars == {"API_BASE": "https://example.com"}
    assert config.language == "python"


@pytest.mark.asyncio
async def test_rule_detail_returns_all_persisted_editable_configuration(monkeypatch) -> None:
    detail = SimpleNamespace(
        engine="scrapy",
        region="cn-east",
        require_render=True,
        target_url="https://example.com",
        url_pattern=None,
        callback_type="list",
        request_method="GET",
        extraction_rules=[{"field": "title", "selector": "h1"}],
        data_schema={"title": "string"},
        pagination_config={"type": "next"},
        max_pages=9,
        start_page=2,
        request_delay=250,
        retry_count=4,
        timeout=45,
        priority=3,
        dont_filter=True,
        headers={"Accept": "application/json"},
        cookies={"session": "value"},
        proxy_config={"enabled": True, "proxy": "http://proxy.invalid"},
        anti_spider={"rotate_ua": True},
        task_config={"concurrency": 2},
        resume_enabled=True,
        dedup_config={"enabled": True, "fields": ["title"]},
    )
    monkeypatch.setattr(
        project_routes.relation_service,
        "get_project_rule_detail",
        AsyncMock(return_value=detail),
    )
    response = SimpleNamespace(file_info=None, code_info=None, rule_info=None)

    await project_routes._attach_project_detail_info(
        response,
        SimpleNamespace(id=1, type=ProjectType.RULE),
    )

    assert response.rule_info["headers"] == detail.headers
    assert response.rule_info["cookies"] == detail.cookies
    assert response.rule_info["proxy_config"] == detail.proxy_config
    assert response.rule_info["anti_spider"] == detail.anti_spider
    assert response.rule_info["task_config"] == detail.task_config
    assert response.rule_info["resume_enabled"] is True
    assert response.rule_info["dedup_config"] == detail.dedup_config


def test_project_detail_route_does_not_cache_decrypted_runtime_configuration() -> None:
    assert not hasattr(project_routes.get_project_detail, "__wrapped__")
