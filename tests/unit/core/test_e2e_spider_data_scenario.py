from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.e2e.spider_data_scenario import (
    CRAWL_TARGET_URL,
    EXPECTED_TITLE,
    SPIDER_NAME,
    _delete_spider_storage,
    assert_expected_spider_item,
    build_rule_project_form,
    list_spider_items,
)


class FakeRedisPipeline:
    def __init__(self, redis) -> None:
        self.redis = redis

    def eval(self, _script: str, numkeys: int, *keys: str) -> None:
        assert numkeys == 5
        self.redis.tombstones.append(keys[0])
        self.redis.deleted.extend(keys[1:])

    def zrem(self, key: str, member: str) -> None:
        self.redis.zremmed.append((key, member))

    async def execute(self) -> None:
        self.redis.remaining = 0


class FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.tombstones: list[str] = []
        self.zremmed: list[tuple[str, str]] = []
        self.remaining = 3

    def pipeline(self, **_kwargs) -> FakeRedisPipeline:
        return FakeRedisPipeline(self)

    async def exists(self, *keys: str) -> int:
        self.checked_keys = keys
        return self.remaining

    async def zscore(self, key: str, member: str):
        self.zscore_checked = (key, member)
        return None


def test_rule_project_form_builds_minimal_real_crawler_config() -> None:
    config = SimpleNamespace(runtime_python_version="3.12", shared_env_name="shared-py312")

    form = build_rule_project_form(config, "worker-1")

    assert form["type"] == "rule"
    assert form["target_url"] == CRAWL_TARGET_URL
    assert form["engine"] == "requests"
    assert '"expr": "h1::text"' in form["extraction_rules"]
    assert form["existing_env_name"] == "shared-py312"


def test_expected_spider_item_requires_transport_context_and_content() -> None:
    item = {
        "run_id": "run-1",
        "project_id": "project-1",
        "spider_name": SPIDER_NAME,
        "sequence": "1",
        "data": {"title": EXPECTED_TITLE},
    }

    assert assert_expected_spider_item([item], run_id="run-1", project_id="project-1") is item


@pytest.mark.parametrize(
    "item",
    [
        {"data": {"title": "wrong"}},
        {"data": EXPECTED_TITLE},
        {},
    ],
)
def test_expected_spider_item_rejects_missing_expected_content(item) -> None:
    with pytest.raises(AssertionError, match="未找到预期抓取数据"):
        assert_expected_spider_item([item], run_id="run-1", project_id="project-1")


@pytest.mark.asyncio
async def test_list_spider_items_rejects_invalid_api_shape(monkeypatch) -> None:
    async def fake_request(*args, **kwargs):
        return {"success": True, "data": {"items": "invalid"}}

    monkeypatch.setattr("tests.e2e.spider_data_scenario.request_json", fake_request)

    with pytest.raises(AssertionError, match="响应格式错误"):
        await list_spider_items(object(), "token", "run-1")


@pytest.mark.asyncio
async def test_spider_storage_cleanup_deletes_stream_meta_and_index(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(
        "antcode_core.infrastructure.redis.get_redis_client",
        AsyncMock(return_value=redis),
    )

    await _delete_spider_storage(["run-1"], "project-1")

    assert any(key.endswith("spider:run-1:data") for key in redis.deleted)
    assert any(key.endswith("spider:run-1:meta") for key in redis.deleted)
    assert any(key.endswith("spider:run-1:item-ids") for key in redis.deleted)
    assert any(key.endswith("spider:run-1:item-order") for key in redis.deleted)
    assert any(key.endswith("spider:run-1:tombstone") for key in redis.tombstones)
    assert any(key.endswith("spider:index:project-1") for key, _ in redis.zremmed)
    assert len(redis.checked_keys) == 4
