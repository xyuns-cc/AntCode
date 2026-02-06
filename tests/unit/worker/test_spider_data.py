"""
爬虫数据存储模块单元测试
"""

import json
from datetime import datetime

import pytest

from antcode_worker.plugins.spider.data.models import (
    SpiderConfig,
    SpiderDataItem,
    SpiderMeta,
)


class TestSpiderDataItem:
    """SpiderDataItem 测试"""

    def test_create_item(self):
        """测试创建数据条目"""
        item = SpiderDataItem(
            run_id="run-001",
            project_id="proj-001",
            spider_name="test_spider",
            data={"title": "Test", "price": 99.9},
            url="https://example.com/item/1",
        )

        assert item.run_id == "run-001"
        assert item.project_id == "proj-001"
        assert item.spider_name == "test_spider"
        assert item.data == {"title": "Test", "price": 99.9}
        assert item.url == "https://example.com/item/1"
        assert item.item_type == "default"
        assert item.item_id  # 自动生成

    def test_to_redis_dict(self):
        """测试转换为 Redis 格式"""
        item = SpiderDataItem(
            item_id="item-001",
            run_id="run-001",
            project_id="proj-001",
            spider_name="test_spider",
            data={"title": "测试", "price": 99.9},
            url="https://example.com",
            sequence=1,
        )

        redis_dict = item.to_redis_dict()

        assert redis_dict["item_id"] == "item-001"
        assert redis_dict["run_id"] == "run-001"
        assert redis_dict["project_id"] == "proj-001"
        assert redis_dict["spider_name"] == "test_spider"
        assert redis_dict["sequence"] == "1"
        # data 应该是 JSON 字符串
        assert json.loads(redis_dict["data"]) == {"title": "测试", "price": 99.9}

    def test_from_redis_dict(self):
        """测试从 Redis 格式恢复"""
        redis_dict = {
            "item_id": "item-001",
            "run_id": "run-001",
            "project_id": "proj-001",
            "spider_name": "test_spider",
            "item_type": "product",
            "data": '{"title": "测试", "price": 99.9}',
            "url": "https://example.com",
            "timestamp": "2026-01-14T10:00:00",
            "sequence": "5",
        }

        item = SpiderDataItem.from_redis_dict(redis_dict)

        assert item.item_id == "item-001"
        assert item.run_id == "run-001"
        assert item.project_id == "proj-001"
        assert item.spider_name == "test_spider"
        assert item.item_type == "product"
        assert item.data == {"title": "测试", "price": 99.9}
        assert item.url == "https://example.com"
        assert item.sequence == 5


class TestSpiderMeta:
    """SpiderMeta 测试"""

    def test_create_meta(self):
        """测试创建元数据"""
        meta = SpiderMeta(
            run_id="run-001",
            project_id="proj-001",
            spider_name="test_spider",
            status="running",
        )

        assert meta.run_id == "run-001"
        assert meta.project_id == "proj-001"
        assert meta.spider_name == "test_spider"
        assert meta.status == "running"
        assert meta.items_count == 0

    def test_to_redis_dict(self):
        """测试转换为 Redis 格式"""
        meta = SpiderMeta(
            run_id="run-001",
            project_id="proj-001",
            spider_name="test_spider",
            status="completed",
            items_count=100,
            pages_count=10,
            errors_count=2,
            duration_ms=5000.5,
            errors=["Error 1", "Error 2"],
        )

        redis_dict = meta.to_redis_dict()

        assert redis_dict["run_id"] == "run-001"
        assert redis_dict["status"] == "completed"
        assert redis_dict["items_count"] == "100"
        assert redis_dict["pages_count"] == "10"
        assert redis_dict["errors_count"] == "2"
        assert redis_dict["duration_ms"] == "5000.5"
        assert json.loads(redis_dict["errors"]) == ["Error 1", "Error 2"]

    def test_from_redis_dict(self):
        """测试从 Redis 格式恢复"""
        redis_dict = {
            "run_id": "run-001",
            "project_id": "proj-001",
            "spider_name": "test_spider",
            "status": "completed",
            "started_at": "2026-01-14T10:00:00",
            "finished_at": "2026-01-14T10:05:00",
            "items_count": "100",
            "pages_count": "10",
            "errors_count": "2",
            "duration_ms": "300000",
            "config": "{}",
            "errors": '["Error 1"]',
        }

        meta = SpiderMeta.from_redis_dict(redis_dict)

        assert meta.run_id == "run-001"
        assert meta.status == "completed"
        assert meta.items_count == 100
        assert meta.pages_count == 10
        assert meta.errors_count == 2
        assert meta.duration_ms == 300000
        assert meta.errors == ["Error 1"]


class TestSpiderConfig:
    """SpiderConfig 测试"""

    def test_create_config(self):
        """测试创建配置"""
        config = SpiderConfig(
            project_id="proj-001",
            dedup_fields=["url", "title"],
            ttl_seconds=3600,
            max_items=5000,
        )

        assert config.project_id == "proj-001"
        assert config.dedup_fields == ["url", "title"]
        assert config.ttl_seconds == 3600
        assert config.max_items == 5000

    def test_to_redis_dict(self):
        """测试转换为 Redis 格式"""
        config = SpiderConfig(
            project_id="proj-001",
            item_schema={"type": "object"},
            dedup_fields=["url"],
            post_processors=["clean_html"],
        )

        redis_dict = config.to_redis_dict()

        assert redis_dict["project_id"] == "proj-001"
        assert json.loads(redis_dict["item_schema"]) == {"type": "object"}
        assert json.loads(redis_dict["dedup_fields"]) == ["url"]
        assert json.loads(redis_dict["post_processors"]) == ["clean_html"]

    def test_from_redis_dict(self):
        """测试从 Redis 格式恢复"""
        redis_dict = {
            "project_id": "proj-001",
            "item_schema": '{"type": "object"}',
            "dedup_fields": '["url", "title"]',
            "ttl_seconds": "7200",
            "max_items": "5000",
            "post_processors": "[]",
            "custom_settings": "{}",
        }

        config = SpiderConfig.from_redis_dict(redis_dict)

        assert config.project_id == "proj-001"
        assert config.item_schema == {"type": "object"}
        assert config.dedup_fields == ["url", "title"]
        assert config.ttl_seconds == 7200
        assert config.max_items == 5000


class TestRedisKeys:
    """Redis Key 生成测试"""

    def test_spider_keys(self):
        """测试爬虫相关 Key 生成"""
        from antcode_worker.transport.redis.keys import RedisKeys

        keys = RedisKeys()

        assert keys.spider_data_stream("run-001") == "antcode:spider:data:run-001"
        assert keys.spider_meta_key("run-001") == "antcode:spider:meta:run-001"
        assert keys.spider_index_key("proj-001") == "antcode:spider:index:proj-001"
        assert keys.spider_config_key("proj-001") == "antcode:spider:config:proj-001"

    def test_custom_namespace(self):
        """测试自定义命名空间"""
        from antcode_worker.transport.redis.keys import RedisKeys

        keys = RedisKeys(namespace="myapp")

        assert keys.spider_data_stream("run-001") == "myapp:spider:data:run-001"
        assert keys.spider_meta_key("run-001") == "myapp:spider:meta:run-001"
