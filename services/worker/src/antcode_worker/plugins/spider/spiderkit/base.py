"""
Spider 基类 - 爬虫定义

用法:
    class MySpider(Spider):
        name = "my_spider"
        start_urls = ["https://example.com"]

        async def parse(self, response):
            for item in response.css("div.item"):
                yield {
                    "title": item.css("h2::text").get(),
                    "link": item.css("a::attr(href)").get(),
                }

            # 翻页
            next_page = response.css("a.next::attr(href)").get()
            if next_page:
                yield Request(next_page, callback=self.parse)

支持 Redis 数据存储:
    spider = MySpider()
    spider.set_data_reporter(reporter)  # 注入数据上报器
    result = await spider.run()
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from .client import ClientConfig, HttpClient
from .request import Request, Response

if TYPE_CHECKING:
    from antcode_worker.plugins.spider.data import SpiderDataReporter


@dataclass
class CrawlResult:
    """爬取结果"""

    spider_name: str
    items: list[dict[str, Any]] = field(default_factory=list)
    requests_count: int = 0
    items_count: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float = 0
    run_id: str = ""
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "spider_name": self.spider_name,
            "items": self.items,
            "requests_count": self.requests_count,
            "items_count": self.items_count,
            "errors": self.errors,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "run_id": self.run_id,
            "project_id": self.project_id,
        }


class Spider(ABC):
    """
    爬虫基类

    属性:
        name: 爬虫名称
        start_urls: 起始 URL 列表
        custom_settings: 自定义配置

    方法:
        start_requests(): 生成起始请求
        parse(response): 解析响应
    """

    name: str = "base_spider"
    start_urls: list[str] = []

    # 自定义配置
    custom_settings: dict[str, Any] = {}

    # 默认配置
    default_headers: dict[str, str] = {}
    default_cookies: dict[str, str] = {}

    # 并发控制
    concurrent_requests: int = 16
    download_delay: float = 0

    def __init__(self, **kwargs):
        """初始化爬虫"""
        self.settings = {**self.custom_settings, **kwargs}
        self._client: HttpClient | None = None
        self._running = False
        self._request_queue: asyncio.Queue = asyncio.Queue()
        self._seen_urls: set = set()

        # 结果
        self._result = CrawlResult(spider_name=self.name)

        # 数据上报器（可选）
        self._data_reporter: SpiderDataReporter | None = None
        self._run_id: str = kwargs.get("run_id", "")
        self._project_id: str = kwargs.get("project_id", "")

    def set_data_reporter(self, reporter: SpiderDataReporter) -> None:
        """
        设置数据上报器

        Args:
            reporter: SpiderDataReporter 实例
        """
        self._data_reporter = reporter

    def set_run_context(self, run_id: str, project_id: str) -> None:
        """
        设置运行上下文

        Args:
            run_id: 运行 ID
            project_id: 项目 ID
        """
        self._run_id = run_id
        self._project_id = project_id
        self._result.run_id = run_id
        self._result.project_id = project_id

    async def start_requests(self) -> AsyncGenerator[Request, None]:
        """
        生成起始请求

        可重写此方法自定义起始请求
        """
        for url in self.start_urls:
            yield Request(
                url=url,
                callback=self.parse,
                headers=self.default_headers.copy(),
                cookies=self.default_cookies.copy(),
            )

    @abstractmethod
    async def parse(
        self, response: Response
    ) -> AsyncGenerator[dict | Request, None]:
        """
        解析响应

        必须重写此方法

        Args:
            response: 响应对象

        Yields:
            字典（数据项）或 Request（后续请求）
        """
        raise NotImplementedError("必须实现 parse 方法")
        yield  # 使其成为生成器

    async def errback(self, request: Request, error: Exception) -> None:
        """
        错误回调

        Args:
            request: 请求对象
            error: 异常
        """
        logger.error(f"请求失败 [{request.url}]: {error}")
        self._result.errors.append(f"{request.url}: {error}")

    async def run(self, client: HttpClient | None = None) -> CrawlResult:
        """
        运行爬虫

        Args:
            client: HTTP 客户端（可选）

        Returns:
            爬取结果
        """
        import time

        start_time = time.time()
        self._result.started_at = datetime.now().isoformat()
        self._result.run_id = self._run_id
        self._result.project_id = self._project_id

        # 初始化客户端
        own_client = False
        if client:
            self._client = client
        else:
            config = ClientConfig(**self.settings.get("client_config", {}))
            self._client = HttpClient(config)
            await self._client.start()
            own_client = True

        self._running = True
        status = "completed"

        try:
            # 添加起始请求
            async for request in self.start_requests():
                await self._request_queue.put(request)

            # 并发处理
            workers = [
                asyncio.create_task(self._worker())
                for _ in range(self.concurrent_requests)
            ]

            # 等待队列清空
            await self._request_queue.join()

            # 停止 workers
            self._running = False
            for worker in workers:
                worker.cancel()

        except Exception as e:
            logger.error(f"爬虫异常: {e}")
            self._result.errors.append(str(e))
            status = "failed"

        finally:
            if own_client and self._client:
                await self._client.close()

        self._result.finished_at = datetime.now().isoformat()
        self._result.duration_ms = (time.time() - start_time) * 1000
        self._result.items_count = len(self._result.items)

        # 完成爬取，写入最终状态到 Redis
        if self._data_reporter:
            await self._finalize_crawl(status)

        return self._result

    async def _finalize_crawl(self, status: str) -> None:
        """完成爬取，写入最终状态"""
        if not self._data_reporter:
            return

        try:
            await self._data_reporter.finalize(
                run_id=self._run_id,
                status=status,
                items_count=self._result.items_count,
                pages_count=self._result.requests_count,
                errors_count=len(self._result.errors),
                duration_ms=self._result.duration_ms,
                errors=self._result.errors if self._result.errors else None,
            )
        except Exception as e:
            logger.error(f"完成爬取状态写入失败: {e}")

    async def _worker(self) -> None:
        """工作协程"""
        while self._running:
            try:
                request = await asyncio.wait_for(
                    self._request_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._process_request(request)
            except Exception as e:
                await self.errback(request, e)
            finally:
                self._request_queue.task_done()

            # 下载延迟
            if self.download_delay > 0:
                await asyncio.sleep(self.download_delay)

    async def _process_request(self, request: Request) -> None:
        """处理请求"""
        # URL 去重
        if not request.dont_filter and request.url in self._seen_urls:
            return
        self._seen_urls.add(request.url)

        self._result.requests_count += 1

        # 发送请求
        response = await self._client.fetch(request)

        if not response.ok:
            if request.errback:
                await request.errback(request, Exception(f"HTTP {response.status}"))
            else:
                await self.errback(request, Exception(f"HTTP {response.status}"))
            return

        # 调用回调
        callback = request.callback or self.parse

        async for result in callback(response, **request.cb_kwargs):
            if isinstance(result, Request):
                # 新请求
                await self._request_queue.put(result)
            elif isinstance(result, dict):
                # 数据项
                self._result.items.append(result)

                # 实时上报到 Redis（如果配置了上报器）
                if self._data_reporter:
                    await self._report_item(result, response.url)
            else:
                logger.warning(f"未知结果类型: {type(result)}")

    async def _report_item(self, data: dict[str, Any], url: str) -> None:
        """上报数据项到 Redis"""
        if not self._data_reporter:
            return

        try:
            from antcode_worker.plugins.spider.data import SpiderDataItem

            item = SpiderDataItem(
                run_id=self._run_id,
                project_id=self._project_id,
                spider_name=self.name,
                data=data,
                url=url,
            )
            await self._data_reporter.report_item(item)
        except Exception as e:
            logger.warning(f"上报数据项失败: {e}")

    def make_request(
        self, url: str, callback: Callable = None, method: str = "GET", **kwargs
    ) -> Request:
        """
        创建请求

        Args:
            url: URL
            callback: 回调函数
            method: 请求方法
            **kwargs: 其他参数

        Returns:
            Request 对象
        """
        return Request(
            url=url,
            method=method,
            callback=callback or self.parse,
            headers={**self.default_headers, **kwargs.pop("headers", {})},
            cookies={**self.default_cookies, **kwargs.pop("cookies", {})},
            **kwargs,
        )
