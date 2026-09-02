"""
Core Redis 客户端单元测试

测试 Redis 键生成和基础功能
"""

from antcode_core.infrastructure.redis.keys import RedisKeys


class TestRedisKeys:
    """Redis 键生成测试"""

    def test_default_namespace(self):
        """测试默认命名空间"""
        keys = RedisKeys()

        assert keys.namespace == "antcode"

    def test_custom_namespace(self):
        """测试自定义命名空间"""
        keys = RedisKeys(namespace="myapp")

        assert keys.namespace == "myapp"

    def test_log_stream_key(self):
        """测试日志流键"""
        keys = RedisKeys()

        key = keys.log_stream_key("exec-001")

        assert "antcode" in key
        assert "log" in key
        assert "exec-001" in key

    def test_spider_keys(self):
        """测试爬虫相关键"""
        keys = RedisKeys()

        data_key = keys.spider_data_stream("run-001")
        meta_key = keys.spider_meta_key("run-001")
        item_ids_key = keys.spider_item_ids_key("run-001")
        item_order_key = keys.spider_item_order_key("run-001")
        tombstone_key = keys.spider_tombstone_key("run-001")
        index_key = keys.spider_index_key("proj-001")
        index_expiry_key = keys.spider_index_expiry_key("proj-001")
        config_key = keys.spider_config_key("proj-001")

        assert data_key == "{antcode}:spider:run-001:data"
        assert meta_key == "{antcode}:spider:run-001:meta"
        assert item_ids_key == "{antcode}:spider:run-001:item-ids"
        assert item_order_key == "{antcode}:spider:run-001:item-order"
        assert tombstone_key == "{antcode}:spider:run-001:tombstone"
        assert index_key == "{antcode}:spider:index:proj-001"
        assert index_expiry_key == "{antcode}:spider:index:expiry:proj-001"
        assert config_key == "antcode:spider:config:proj-001"
