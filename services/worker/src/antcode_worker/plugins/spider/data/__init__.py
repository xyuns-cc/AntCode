"""
爬虫数据存储模块

提供爬虫数据的 Redis 存储和上报功能：
- models: 数据模型（SpiderDataItem、SpiderMeta、SpiderConfig）
- reporter: 数据上报器（RedisDataReporter、GatewayDataReporter）
- reader: 数据读取器（供后续落库使用）
"""

from antcode_worker.plugins.spider.data.models import (
    SpiderConfig,
    SpiderDataItem,
    SpiderMeta,
)
from antcode_worker.plugins.spider.data.reader import SpiderDataReader
from antcode_worker.plugins.spider.data.reporter import (
    GatewayDataReporter,
    RedisDataReporter,
    SpiderDataReporter,
    create_data_reporter,
)

__all__ = [
    # Models
    "SpiderDataItem",
    "SpiderMeta",
    "SpiderConfig",
    # Reporter
    "SpiderDataReporter",
    "RedisDataReporter",
    "GatewayDataReporter",
    "create_data_reporter",
    # Reader
    "SpiderDataReader",
]
