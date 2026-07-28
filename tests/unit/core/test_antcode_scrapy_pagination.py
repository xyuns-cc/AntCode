"""Rule 分页配置从 API 到 Scrapy request 的契约测试。"""

from __future__ import annotations

import json

import pytest
from antcode_core.domain.models.project import ProjectRule
from antcode_core.domain.schemas.project import PaginationConfig, ProjectRuleCreateRequest
from antcode_scrapy.settings import build_settings
from antcode_scrapy.spiders.rule_spider import UniversalRuleSpider
from pydantic import ValidationError


def _rule(method: str, **pagination) -> dict:
    return {
        "engine": "requests",
        "target_url": "https://example.com/list",
        "extraction_rules": [{"desc": "title", "type": "css", "expr": "h1::text"}],
        "pagination_config": {"method": method, **pagination},
    }


def test_pagination_schema_preserves_frontend_fields_and_selector() -> None:
    config = PaginationConfig.model_validate(
        {
            "method": "js_click",
            "start_page": 0,
            "max_pages": 3,
            "next_page_rule": {"type": "text", "expr": "下一页"},
            "wait_after_click_ms": 1200,
            "url_template": "https://example.com/page/{page}",
            "page_param": "offset",
            "scroll_count": 7,
            "scroll_wait_ms": 600,
        }
    )

    payload = config.model_dump(exclude_none=True)
    assert payload["start_page"] == 0
    assert payload["next_page_rule"] == {"type": "text", "expr": "下一页"}
    assert payload["url_template"].endswith("/{page}")
    assert payload["page_param"] == "offset"
    assert payload["scroll_count"] == 7
    assert payload["scroll_wait_ms"] == 600


def test_pagination_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PaginationConfig.model_validate({"method": "none", "silently_ignored": True})


def test_rule_project_create_preserves_structured_pagination_json() -> None:
    request = ProjectRuleCreateRequest(
        name="rule-project",
        type="rule",
        runtime_scope="shared",
        worker_id="worker-1",
        use_existing_env=True,
        existing_env_name="shared-py312",
        target_url="https://example.com/list",
        extraction_rules=[{"desc": "title", "type": "css", "expr": "h1::text"}],
        pagination_config=json.dumps(
            {
                "method": "click_element",
                "start_page": 0,
                "page_param": "offset",
                "next_page_rule": {"type": "xpath", "expr": "//a[@rel='next']"},
            }
        ),
    )

    pagination = request.pagination_config.model_dump(exclude_none=True)
    assert pagination["start_page"] == 0
    assert pagination["page_param"] == "offset"
    assert pagination["next_page_rule"] == {"type": "xpath", "expr": "//a[@rel='next']"}


@pytest.mark.parametrize("method", ["infinite_scroll", "javascript", "ajax"])
def test_scroll_methods_enable_playwright_for_requests_engine(method: str) -> None:
    rule = _rule(method, scroll_count=2, scroll_wait_ms=10)

    settings = build_settings(rule)
    spider = UniversalRuleSpider(rule=rule, run_id="run-1", project_id="project-1")
    request = spider._build_request(rule["target_url"], page_number=0)

    assert "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler" in settings["DOWNLOAD_HANDLERS"].values()
    assert request.meta["playwright"] is True
    assert request.meta["playwright_include_page"] is True
    assert request.meta["antcode_page_number"] == 0
    assert len(request.meta["playwright_page_methods"]) == 5


@pytest.mark.asyncio
async def test_url_param_pagination_starts_at_zero() -> None:
    rule = _rule("url_param", start_page=0, max_pages=2, page_param="offset")
    spider = UniversalRuleSpider(rule=rule, run_id="run-1", project_id="project-1")

    requests = [request async for request in spider.start()]

    assert [request.url for request in requests] == [
        "https://example.com/list?offset=0",
        "https://example.com/list?offset=1",
    ]
    assert ProjectRule(target_url=rule["target_url"], start_page=0).to_dispatch_dict()["start_page"] == 0
