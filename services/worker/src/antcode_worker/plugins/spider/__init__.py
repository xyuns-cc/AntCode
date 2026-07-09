"""爬虫任务插件。

- ``SpiderPlugin``：TaskType.SPIDER 类型的历史插件（保留供旧任务复用）。
- ``data``：爬虫数据模型（SpiderDataItem 等）与旧上报器实现。

S4 后 spiderkit 子包已删除；规则爬虫（TaskType.RULE）走
:mod:`antcode_scrapy`，通过 :mod:`antcode_worker.plugins.rule.plugin`
生成 Scrapy 子进程 ExecPlan。
"""

from antcode_worker.plugins.spider import data
from antcode_worker.plugins.spider.plugin import SpiderPlugin

__all__ = ["SpiderPlugin", "data"]
