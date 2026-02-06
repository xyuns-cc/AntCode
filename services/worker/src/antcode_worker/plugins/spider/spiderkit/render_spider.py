"""
RenderSpider 基类 - 浏览器渲染爬虫

用法:
    class MyRenderSpider(RenderSpider):
        name = "my_render_spider"
        start_urls = ["https://example.com"]

        async def parse(self, response: RenderResponse):
            # 使用 lxml/parsel 解析 HTML
            for item in self.css(response.html, "div.item"):
                yield {
                    "title": self.css_first(item, "h2::text"),
                    "link": self.css_first(item, "a::attr(href)"),
                }

            # 交互式翻页
            next_response = await self.interact(
                response.url,
                [{"action": "click", "selector": "a.next"}]
            )
            if next_response.ok:
                async for item in self.parse(next_response):
                    yield item
"""

import asyncio
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

try:
    from lxml import etree
    from parsel import Selector

    HAS_PARSER = True
except ImportError:
    HAS_PARSER = False
    etree = None
    Selector = None

from .render_client import RenderClient, RenderConfig, RenderResponse


@dataclass
class RenderCrawlResult:
    """渲染爬取结果"""

    spider_name: str
    items: list[dict[str, Any]] = field(default_factory=list)
    pages_rendered: int = 0
    items_count: int = 0
    errors: list[str] = field(default_factory=list)
    screenshots: list[bytes] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "spider_name": self.spider_name,
            "items": self.items,
            "pages_rendered": self.pages_rendered,
            "items_count": self.items_count,
            "errors": self.errors,
            "screenshots_count": len(self.screenshots),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }


class RenderSpider(ABC):
    """
    渲染爬虫基类

    属性:
        name: 爬虫名称
        start_urls: 起始 URL 列表
        browser_id: 指定浏览器实例（用于会话保持）
        wait_selector: 默认等待元素
        screenshot_on_error: 错误时截图
    """

    name: str = "render_spider"
    start_urls: list[str] = []

    # 浏览器配置
    browser_id: str | None = None
    wait_selector: str | None = None
    screenshot_on_error: bool = True

    # 并发控制
    concurrent_pages: int = 1
    page_delay: float = 1.0

    # 自定义配置
    custom_settings: dict[str, Any] = {}

    def __init__(self, **kwargs):
        """初始化爬虫"""
        self.settings = {**self.custom_settings, **kwargs}
        self._client: RenderClient | None = None
        self._running = False
        self._result = RenderCrawlResult(spider_name=self.name)

        # 从 kwargs 更新属性
        for key in ["browser_id", "wait_selector", "concurrent_pages", "page_delay"]:
            if key in kwargs:
                setattr(self, key, kwargs[key])

    # ==================== 生命周期方法 ====================

    async def start_requests(self) -> AsyncGenerator[str, None]:
        """
        生成起始请求

        可重写此方法自定义起始 URL
        """
        for url in self.start_urls:
            yield url

    @abstractmethod
    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """
        解析响应

        必须重写此方法

        Args:
            response: 渲染响应

        Yields:
            字典（数据项）或字符串（后续 URL）
        """
        raise NotImplementedError("必须实现 parse 方法")
        yield  # 使其成为生成器

    async def errback(self, url: str, error: str) -> None:
        """
        错误回调

        Args:
            url: 请求 URL
            error: 错误信息
        """
        logger.error(f"渲染失败 [{url}]: {error}")
        self._result.errors.append(f"{url}: {error}")

    # ==================== 核心方法 ====================

    async def run(self, client: RenderClient | None = None) -> RenderCrawlResult:
        """
        运行爬虫

        Args:
            client: 渲染客户端（可选）

        Returns:
            爬取结果
        """
        start_time = time.time()
        self._result.started_at = datetime.now().isoformat()

        # 初始化客户端
        own_client = False
        if client:
            self._client = client
        else:
            config = RenderConfig(**self.settings.get("render_config", {}))
            self._client = RenderClient(config)
            await self._client.start()
            own_client = True

        self._running = True

        try:
            # 收集起始 URL
            urls = []
            async for url in self.start_requests():
                urls.append(url)

            # 处理 URL
            for url in urls:
                if not self._running:
                    break

                await self._process_url(url)

                # 页面延迟
                if self.page_delay > 0:
                    await asyncio.sleep(self.page_delay)

        except Exception as e:
            logger.error(f"爬虫异常: {e}")
            self._result.errors.append(str(e))

        finally:
            if own_client and self._client:
                await self._client.close()

        self._result.finished_at = datetime.now().isoformat()
        self._result.duration_ms = (time.time() - start_time) * 1000
        self._result.items_count = len(self._result.items)

        return self._result

    async def _process_url(self, url: str) -> None:
        """处理单个 URL"""
        response = await self._client.render(
            url,
            wait=self.wait_selector,
            screenshot=self.screenshot_on_error,
            browser_id=self.browser_id,
        )

        self._result.pages_rendered += 1

        if not response.ok:
            await self.errback(url, response.error or "Unknown error")
            if response.screenshot:
                self._result.screenshots.append(response.screenshot)
            return

        # 解析响应
        try:
            async for result in self.parse(response):
                if isinstance(result, dict):
                    self._result.items.append(result)
                elif isinstance(result, str) and self._running:
                    # 后续 URL
                    await self._process_url(result)
        except Exception as e:
            await self.errback(url, str(e))

    # ==================== 便捷方法 ====================

    async def render(
        self,
        url: str,
        *,
        wait: str | None = None,
        screenshot: bool = False,
    ) -> RenderResponse:
        """
        渲染页面

        Args:
            url: 目标 URL
            wait: 等待元素
            screenshot: 是否截图
        """
        return await self._client.render(
            url,
            wait=wait or self.wait_selector,
            screenshot=screenshot,
            browser_id=self.browser_id,
        )

    async def interact(
        self,
        url: str,
        actions: list[dict[str, Any]],
    ) -> RenderResponse:
        """
        交互式操作

        Args:
            url: 目标 URL
            actions: 操作列表
        """
        return await self._client.interact(
            url,
            actions,
            browser_id=self.browser_id,
        )

    async def execute_js(self, url: str, script: str) -> dict[str, Any]:
        """
        执行 JavaScript

        Args:
            url: 目标 URL
            script: JS 代码
        """
        return await self._client.execute_script(
            url,
            script,
            browser_id=self.browser_id,
        )

    # ==================== 解析辅助方法 ====================

    def css(self, html: str, query: str) -> list[str]:
        """CSS 选择器"""
        if not HAS_PARSER:
            logger.warning("parsel 未安装，CSS 选择器不可用")
            return []

        sel = Selector(text=html)
        return sel.css(query).getall()

    def css_first(self, html: str, query: str, default: str = "") -> str:
        """CSS 选择器（第一个）"""
        if not HAS_PARSER:
            return default

        sel = Selector(text=html)
        return sel.css(query).get(default=default)

    def xpath(self, html: str, query: str) -> list[str]:
        """XPath 选择器"""
        if not HAS_PARSER:
            logger.warning("lxml 未安装，XPath 选择器不可用")
            return []

        sel = Selector(text=html)
        return sel.xpath(query).getall()

    def xpath_first(self, html: str, query: str, default: str = "") -> str:
        """XPath 选择器（第一个）"""
        if not HAS_PARSER:
            return default

        sel = Selector(text=html)
        return sel.xpath(query).get(default=default)

    def re_find(self, text: str, pattern: str, flags: int = 0) -> list[str]:
        """正则匹配"""
        return re.findall(pattern, text, flags)

    def re_first(
        self, text: str, pattern: str, default: str = "", flags: int = 0
    ) -> str:
        """正则匹配（第一个）"""
        match = re.search(pattern, text, flags)
        return (
            match.group(1)
            if match and match.groups()
            else (match.group() if match else default)
        )
