import pytest
from antcode_core.common.config import settings
from antcode_core.domain.schemas.crawl import CrawlBatchCreateRequest
from antcode_core.domain.schemas.system_config import SystemConfigBatchUpdate
from antcode_web_api.routes.v1.runtime_models import PackageRequest
from antcode_web_api.routes.v1.tasks import TaskBatchRequest
from antcode_web_api.utils.batch_inputs import bounded_distinct_ids
from antcode_web_api.utils.csv_security import sanitize_csv_cell
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize(
    "value",
    ["=SUM(A1:A2)", " +cmd", "\t-2+3", "\ufeff@IMPORTXML(A1)", "\u2003=1+1"],
)
def test_csv_cell_formula_is_neutralized(value):
    assert sanitize_csv_cell(value) == "'" + value


def test_csv_numeric_and_plain_text_values_are_unchanged():
    assert sanitize_csv_cell(-1) == -1
    assert sanitize_csv_cell("plain") == "plain"


def test_management_batch_ids_are_bounded_before_deduplication(monkeypatch):
    monkeypatch.setattr(settings, "API_MANAGEMENT_BATCH_MAX_ITEMS", 2)
    with pytest.raises(HTTPException, match="单次上限"):
        bounded_distinct_ids(["a", "a", "a"], "ids")


def test_management_batch_ids_reject_duplicates_explicitly():
    with pytest.raises(HTTPException, match="重复"):
        bounded_distinct_ids(["a", "b", "a"], "ids")


def test_task_and_runtime_package_batches_reject_duplicates():
    with pytest.raises(ValidationError, match="不允许重复"):
        TaskBatchRequest(task_ids=["a", "a", "b"], action="start")
    with pytest.raises(ValidationError, match="不允许重复"):
        PackageRequest(packages=["httpx", "httpx"])


def test_task_batch_rejects_more_than_explicit_limit():
    with pytest.raises(ValidationError):
        TaskBatchRequest(task_ids=[str(index) for index in range(101)], action="start")


def test_system_config_batch_rejects_duplicate_keys():
    with pytest.raises(ValidationError, match="重复 config_key"):
        SystemConfigBatchUpdate(
            configs=[
                {"config_key": "alpha", "config_value": "1"},
                {"config_key": "alpha", "config_value": "2"},
            ]
        )


def test_crawl_seed_urls_reject_duplicates():
    with pytest.raises(ValidationError, match="seed_urls 不允许重复"):
        CrawlBatchCreateRequest(project_id="p", name="batch", seed_urls=["https://a", "https://a"])
