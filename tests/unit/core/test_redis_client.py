"""
Core Redis 客户端单元测试

测试 Redis 键生成和基础功能
"""

import pytest

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

    def test_task_ready_stream(self):
        """测试任务就绪流键"""
        keys = RedisKeys()
        
        key = keys.task_ready_stream("worker-001")
        
        assert "antcode" in key
        assert "worker-001" in key
        assert "ready" in key

    def test_task_result_stream(self):
        """测试任务结果流键"""
        keys = RedisKeys()
        
        key = keys.task_result_stream()
        
        assert "antcode" in key
        assert "result" in key

    def test_heartbeat_key(self):
        """测试心跳键"""
        keys = RedisKeys()
        
        key = keys.heartbeat_key("worker-001")
        
        assert "antcode" in key
        assert "heartbeat" in key
        assert "worker-001" in key

    def test_log_stream_key(self):
        """测试日志流键"""
        keys = RedisKeys()
        
        key = keys.log_stream_key("exec-001")
        
        assert "antcode" in key
        assert "log" in key
        assert "exec-001" in key

    def test_consumer_group_name(self):
        """测试消费者组名"""
        keys = RedisKeys()
        
        group = keys.consumer_group_name()
        
        assert "workers" in group

    def test_spider_keys(self):
        """测试爬虫相关键"""
        keys = RedisKeys()
        
        data_key = keys.spider_data_stream("run-001")
        meta_key = keys.spider_meta_key("run-001")
        index_key = keys.spider_index_key("proj-001")
        config_key = keys.spider_config_key("proj-001")
        
        assert "spider" in data_key
        assert "spider" in meta_key
        assert "spider" in index_key
        assert "spider" in config_key
