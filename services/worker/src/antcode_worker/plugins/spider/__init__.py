"""
爬虫任务插件

包含:
- SpiderPlugin: 爬虫任务 ExecPlan 生成器
- spiderkit: 完整爬虫框架（Spider、HttpClient、RenderClient 等）
- data: 爬虫数据存储模块（Redis 存储、上报器、读取器）
"""

# 导出 spiderkit 子模块
# 导出数据存储模块
from antcode_worker.plugins.spider import data, spiderkit
from antcode_worker.plugins.spider.plugin import SpiderPlugin

__all__ = ["SpiderPlugin", "spiderkit", "data"]
