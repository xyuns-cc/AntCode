"""
SpiderKit - 爬虫核心框架

组件:
- Request/Response: 请求响应对象
- Selector: XPath/CSS/正则解析器（基于 lxml）
- Spider: 爬虫基类
- HttpClient: 异步 HTTP 客户端（httpx + curl_cffi）
- Middlewares: 爬虫中间件（UA轮换、代理、限速、指纹伪装）

用法:
    from antcode_worker.plugins.spider.spiderkit import Spider, Request, Response

    class MySpider(Spider):
        name = "my_spider"
        start_urls = ["https://example.com"]

        async def parse(self, response):
            for item in response.css("div.item"):
                yield {
                    "title": item.css("h2::text").get(),
                    "link": item.css("a::attr(href)").get(),
                }
"""

from .base import CrawlResult, Spider
from .client import ClientConfig, HttpClient
from .middlewares import (
    CookieMiddleware,
    ImpersonateMiddleware,
    ProxyMiddleware,
    RateLimitMiddleware,
    RetryMiddleware,
    SpiderMiddleware,
    SpiderMiddlewareManager,
    UserAgentMiddleware,
)
from .request import Request, RequestMethod, Response
from .selector import Selector, SelectorList

__all__ = [
    # 请求响应
    "Request",
    "Response",
    "RequestMethod",
    # 选择器
    "Selector",
    "SelectorList",
    # 爬虫
    "Spider",
    "CrawlResult",
    # HTTP 客户端
    "HttpClient",
    "ClientConfig",
    # 中间件
    "SpiderMiddleware",
    "SpiderMiddlewareManager",
    "UserAgentMiddleware",
    "ProxyMiddleware",
    "RetryMiddleware",
    "RateLimitMiddleware",
    "CookieMiddleware",
    "ImpersonateMiddleware",
]
