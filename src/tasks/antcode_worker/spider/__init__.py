"""
Spider 模块 - 爬虫核心

组件:
- Request/Response: 请求响应对象
- Selector: XPath/CSS/正则解析器（基于 lxml）
- Spider: 爬虫基类
- HttpClient: 异步 HTTP 客户端（httpx + curl_cffi）
- Middlewares: 爬虫中间件（UA轮换、代理、限速、指纹伪装）
- RenderClient: DrissionPage 浏览器渲染客户端
- RenderSpider: 渲染爬虫基类

用法:
    from antcode_worker.engine.spider import Spider, Request, Response
    
    class MySpider(Spider):
        name = "my_spider"
        start_urls = ["https://example.com"]
        
        async def parse(self, response):
            for item in response.css("div.item"):
                yield {
                    "title": item.css("h2::text").get(),
                    "link": item.css("a::attr(href)").get(),
                }
    
    # 渲染爬虫
    from antcode_worker.engine.spider import RenderSpider, RenderResponse
    
    class MyRenderSpider(RenderSpider):
        name = "my_render_spider"
        start_urls = ["https://spa-example.com"]
        wait_selector = "#content"
        
        async def parse(self, response: RenderResponse):
            yield {"title": self.css_first(response.html, "h1::text")}
"""

from .request import Request, Response, RequestMethod
from .selector import Selector, SelectorList
from .base import Spider, CrawlResult
from .client import HttpClient, ClientConfig
from .middlewares import (
    SpiderMiddleware,
    SpiderMiddlewareManager,
    UserAgentMiddleware,
    ProxyMiddleware,
    RetryMiddleware,
    RateLimitMiddleware,
    CookieMiddleware,
    ImpersonateMiddleware,
)
from .render_client import RenderClient, RenderConfig, RenderResponse, BrowserPool
from .render_spider import RenderSpider, RenderCrawlResult

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
    # 渲染客户端
    "RenderClient",
    "RenderConfig",
    "RenderResponse",
    "BrowserPool",
    # 渲染爬虫
    "RenderSpider",
    "RenderCrawlResult",
]
