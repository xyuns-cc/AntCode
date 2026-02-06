"""
爬虫中间件 - 请求/响应处理

内置中间件:
- UserAgentMiddleware: UA 轮换
- ProxyMiddleware: 代理管理
- RetryMiddleware: 重试
- RateLimitMiddleware: 限速
- CookieMiddleware: Cookie 管理
- ImpersonateMiddleware: curl_cffi 指纹轮换
"""

import asyncio
import random
import time
from abc import ABC, abstractmethod

from loguru import logger

from .request import Request, Response


class SpiderMiddleware(ABC):
    """爬虫中间件基类"""

    name: str = "BaseMiddleware"
    priority: int = 500
    enabled: bool = True

    @abstractmethod
    async def process_request(self, request: Request) -> Request | None:
        """
        处理请求

        Returns:
            Request: 继续处理
            None: 跳过请求
        """
        return request

    @abstractmethod
    async def process_response(
        self, request: Request, response: Response
    ) -> Response | None:
        """
        处理响应

        Returns:
            Response: 继续处理
            None: 丢弃响应
        """
        return response

    @abstractmethod
    async def process_exception(
        self, request: Request, exception: Exception
    ) -> Request | None:
        """
        处理异常

        Returns:
            Request: 重试请求
            None: 放弃
        """
        return None


# ============ User-Agent 中间件 ============

# 常用 User-Agent 列表
USER_AGENTS = [
    # Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]


class UserAgentMiddleware(SpiderMiddleware):
    """User-Agent 轮换中间件"""

    name = "UserAgent"
    priority = 100

    def __init__(self, user_agents: list[str] = None, rotate: bool = True):
        self.user_agents = user_agents or USER_AGENTS
        self.rotate = rotate
        self._index = 0

    async def process_request(self, request: Request) -> Request:
        if "User-Agent" not in request.headers:
            if self.rotate:
                ua = random.choice(self.user_agents)
            else:
                ua = self.user_agents[self._index % len(self.user_agents)]
                self._index += 1
            request.headers["User-Agent"] = ua
        return request


# ============ 代理中间件 ============


class ProxyMiddleware(SpiderMiddleware):
    """代理中间件"""

    name = "Proxy"
    priority = 200

    def __init__(self, proxies: list[str] = None, rotate: bool = True):
        self.proxies = proxies or []
        self.rotate = rotate
        self._index = 0
        self._failed_proxies: set[str] = set()

    def add_proxy(self, proxy: str) -> None:
        """添加代理"""
        if proxy not in self.proxies:
            self.proxies.append(proxy)

    def remove_proxy(self, proxy: str) -> None:
        """移除代理"""
        if proxy in self.proxies:
            self.proxies.remove(proxy)
        self._failed_proxies.discard(proxy)

    async def process_request(self, request: Request) -> Request:
        if not self.proxies or request.proxy:
            return request

        # 过滤失败代理
        available = [p for p in self.proxies if p not in self._failed_proxies]
        if not available:
            self._failed_proxies.clear()  # 重置
            available = self.proxies

        if self.rotate:
            request.proxy = random.choice(available)
        else:
            request.proxy = available[self._index % len(available)]
            self._index += 1

        return request

    async def process_exception(
        self, request: Request, exception: Exception
    ) -> Request | None:
        # 标记失败代理
        if request.proxy:
            self._failed_proxies.add(request.proxy)
            logger.warning(f"代理失败: {request.proxy}")
        return None


# ============ 重试中间件 ============


class RetryMiddleware(SpiderMiddleware):
    """重试中间件"""

    name = "Retry"
    priority = 300

    def __init__(
        self,
        max_retries: int = 3,
        retry_codes: list[int] = None,
        retry_delay: float = 1.0,
    ):
        self.max_retries = max_retries
        self.retry_codes = retry_codes or [500, 502, 503, 504, 408, 429]
        self.retry_delay = retry_delay

    async def process_response(
        self, request: Request, response: Response
    ) -> Response | None:
        if response.status in self.retry_codes and request.retry_count < self.max_retries:
            request.retry_count += 1
            await asyncio.sleep(self.retry_delay * request.retry_count)
            logger.debug(
                f"重试 {request.retry_count}/{self.max_retries}: {request.url}"
            )
            return None  # 触发重试
        return response

    async def process_exception(
        self, request: Request, exception: Exception
    ) -> Request | None:
        if request.retry_count < self.max_retries:
            request.retry_count += 1
            await asyncio.sleep(self.retry_delay * request.retry_count)
            return request
        return None


# ============ 限速中间件 ============


class RateLimitMiddleware(SpiderMiddleware):
    """限速中间件"""

    name = "RateLimit"
    priority = 50

    def __init__(
        self,
        requests_per_second: float = 10.0,
        burst: int = 20,
        per_domain: bool = True,
    ):
        self.rate = requests_per_second
        self.burst = burst
        self.per_domain = per_domain

        self._tokens: dict[str, float] = {}
        self._last_update: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _get_domain(self, url: str) -> str:
        """提取域名"""
        from urllib.parse import urlparse

        return urlparse(url).netloc if self.per_domain else "__global__"

    async def process_request(self, request: Request) -> Request:
        domain = self._get_domain(request.url)

        async with self._lock:
            now = time.time()

            # 初始化
            if domain not in self._tokens:
                self._tokens[domain] = self.burst
                self._last_update[domain] = now

            # 补充令牌
            elapsed = now - self._last_update[domain]
            self._tokens[domain] = min(
                self.burst, self._tokens[domain] + elapsed * self.rate
            )
            self._last_update[domain] = now

            # 等待令牌
            if self._tokens[domain] < 1:
                wait_time = (1 - self._tokens[domain]) / self.rate
                await asyncio.sleep(wait_time)
                self._tokens[domain] = 0
            else:
                self._tokens[domain] -= 1

        return request


# ============ Cookie 中间件 ============


class CookieMiddleware(SpiderMiddleware):
    """Cookie 管理中间件"""

    name = "Cookie"
    priority = 150

    def __init__(self):
        self._cookies: dict[str, dict[str, str]] = {}  # domain -> cookies

    def _get_domain(self, url: str) -> str:
        from urllib.parse import urlparse

        return urlparse(url).netloc

    async def process_request(self, request: Request) -> Request:
        domain = self._get_domain(request.url)
        if domain in self._cookies:
            request.cookies = {**self._cookies[domain], **request.cookies}
        return request

    async def process_response(
        self, request: Request, response: Response
    ) -> Response:
        if response.cookies:
            domain = self._get_domain(request.url)
            if domain not in self._cookies:
                self._cookies[domain] = {}
            self._cookies[domain].update(response.cookies)
        return response


# ============ curl_cffi 指纹中间件 ============

IMPERSONATES = [
    "chrome110",
    "chrome107",
    "chrome104",
    "chrome101",
    "chrome100",
    "chrome99",
    "edge101",
    "edge99",
    "safari15_5",
    "safari15_3",
]


class ImpersonateMiddleware(SpiderMiddleware):
    """curl_cffi 指纹轮换中间件"""

    name = "Impersonate"
    priority = 80

    def __init__(
        self,
        impersonates: list[str] = None,
        rotate: bool = True,
        default: str = "chrome110",
    ):
        self.impersonates = impersonates or IMPERSONATES
        self.rotate = rotate
        self.default = default
        self._index = 0

    async def process_request(self, request: Request) -> Request:
        if request.impersonate is None:
            if self.rotate:
                request.impersonate = random.choice(self.impersonates)
            else:
                request.impersonate = self.impersonates[
                    self._index % len(self.impersonates)
                ]
                self._index += 1
        return request


# ============ 中间件管理器 ============


class SpiderMiddlewareManager:
    """爬虫中间件管理器"""

    def __init__(self):
        self._middlewares: list[SpiderMiddleware] = []

    def add(self, middleware: SpiderMiddleware) -> None:
        """添加中间件"""
        self._middlewares.append(middleware)
        self._middlewares.sort(key=lambda m: m.priority)

    def remove(self, name: str) -> bool:
        """移除中间件"""
        for i, m in enumerate(self._middlewares):
            if m.name == name:
                self._middlewares.pop(i)
                return True
        return False

    async def process_request(self, request: Request) -> Request | None:
        """处理请求"""
        for middleware in self._middlewares:
            if not middleware.enabled:
                continue
            try:
                request = await middleware.process_request(request)
                if request is None:
                    return None
            except Exception as e:
                logger.error(f"中间件 {middleware.name} 请求处理异常: {e}")
        return request

    async def process_response(
        self, request: Request, response: Response
    ) -> Response | None:
        """处理响应"""
        for middleware in reversed(self._middlewares):
            if not middleware.enabled:
                continue
            try:
                response = await middleware.process_response(request, response)
                if response is None:
                    return None
            except Exception as e:
                logger.error(f"中间件 {middleware.name} 响应处理异常: {e}")
        return response

    async def process_exception(
        self, request: Request, exception: Exception
    ) -> Request | None:
        """处理异常"""
        for middleware in self._middlewares:
            if not middleware.enabled:
                continue
            try:
                result = await middleware.process_exception(request, exception)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(f"中间件 {middleware.name} 异常处理异常: {e}")
        return None
