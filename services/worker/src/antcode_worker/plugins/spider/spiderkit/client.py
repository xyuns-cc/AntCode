"""
HTTP 客户端 - 异步请求

支持:
- httpx: 默认客户端
- curl_cffi: 浏览器指纹伪装（可选）
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from .request import Request, RequestMethod, Response

# 尝试导入 curl_cffi
try:
    from curl_cffi.requests import AsyncSession as CurlSession

    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    CurlSession = None


# 浏览器指纹列表
BROWSER_IMPERSONATES = [
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


@dataclass
class ClientConfig:
    """客户端配置"""

    # 超时
    timeout: float = 30.0
    connect_timeout: float = 10.0

    # 并发
    max_connections: int = 100
    max_keepalive: int = 20

    # 重试
    max_retries: int = 3
    retry_delay: float = 1.0

    # 代理
    proxy: str | None = None

    # 指纹伪装
    impersonate: str | None = None  # 使用 curl_cffi
    rotate_impersonate: bool = False  # 轮换指纹

    # HTTP/2
    http2: bool = True

    # 验证
    verify_ssl: bool = True

    # 默认 Headers
    default_headers: dict[str, str] = field(
        default_factory=lambda: {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
    )


class HttpClient:
    """
    异步 HTTP 客户端

    优先使用 curl_cffi（如果配置了 impersonate），否则使用 httpx

    用法:
        client = HttpClient(ClientConfig(impersonate="chrome110"))
        await client.start()

        response = await client.fetch(Request(url="https://example.com"))

        await client.close()
    """

    def __init__(self, config: ClientConfig | None = None):
        self.config = config or ClientConfig()

        self._httpx_client: httpx.AsyncClient | None = None
        self._curl_session: Any | None = None
        self._running = False

        # 指纹轮换索引
        self._impersonate_index = 0

        # 统计
        self._stats = {
            "requests": 0,
            "success": 0,
            "failed": 0,
            "retried": 0,
            "bytes_received": 0,
        }

    @property
    def use_curl(self) -> bool:
        """是否使用 curl_cffi"""
        return HAS_CURL_CFFI and self.config.impersonate is not None

    async def start(self) -> None:
        """启动客户端"""
        self._running = True

        # 初始化 httpx
        self._httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.config.connect_timeout,
                read=self.config.timeout,
                write=self.config.timeout,
                pool=self.config.timeout,
            ),
            limits=httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive,
            ),
            http2=self.config.http2,
            verify=self.config.verify_ssl,
            follow_redirects=True,
        )

        # 初始化 curl_cffi（如果可用且配置了指纹）
        if self.use_curl:
            impersonate = self._get_impersonate()
            self._curl_session = CurlSession(impersonate=impersonate)
            logger.info(f"使用 curl_cffi 指纹: {impersonate}")

        logger.debug(f"HTTP 客户端已启动 (curl_cffi: {self.use_curl})")

    async def close(self) -> None:
        """关闭客户端"""
        self._running = False

        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

        if self._curl_session:
            await self._curl_session.close()
            self._curl_session = None

        logger.debug("HTTP 客户端已关闭")

    def _get_impersonate(self) -> str:
        """获取指纹"""
        if self.config.rotate_impersonate:
            impersonate = BROWSER_IMPERSONATES[
                self._impersonate_index % len(BROWSER_IMPERSONATES)
            ]
            self._impersonate_index += 1
            return impersonate
        return self.config.impersonate or "chrome110"

    async def fetch(self, request: Request) -> Response:
        """
        发送请求

        Args:
            request: 请求对象

        Returns:
            响应对象
        """
        self._stats["requests"] += 1
        start_time = time.time()

        # 合并 headers
        headers = {**self.config.default_headers, **request.headers}

        # 选择客户端
        use_curl = request.impersonate is not None or self.use_curl

        try:
            if use_curl and HAS_CURL_CFFI:
                response = await self._fetch_curl(request, headers)
            else:
                response = await self._fetch_httpx(request, headers)

            response.elapsed_ms = (time.time() - start_time) * 1000
            response.request = request
            response.meta = request.meta.copy()

            self._stats["success"] += 1
            self._stats["bytes_received"] += len(response.content)

            return response

        except Exception as e:
            self._stats["failed"] += 1

            # 重试
            if request.retry_count < request.max_retries:
                request.retry_count += 1
                self._stats["retried"] += 1
                await asyncio.sleep(self.config.retry_delay * request.retry_count)
                return await self.fetch(request)

            # 返回错误响应
            return Response(
                url=request.url,
                status=0,
                content=str(e).encode(),
                request=request,
                meta=request.meta.copy(),
                elapsed_ms=(time.time() - start_time) * 1000,
            )

    async def _fetch_httpx(
        self, request: Request, headers: dict[str, str]
    ) -> Response:
        """使用 httpx 请求"""
        proxy = request.proxy or self.config.proxy

        kwargs = {
            "method": request.method.value,
            "url": request.url,
            "headers": headers,
            "cookies": request.cookies or None,
            "params": request.params or None,
            "timeout": request.timeout,
        }

        if proxy:
            kwargs["proxy"] = proxy

        if request.data:
            kwargs["data"] = request.data
        if request.json:
            kwargs["json"] = request.json

        resp = await self._httpx_client.request(**kwargs)

        return Response(
            url=str(resp.url),
            status=resp.status_code,
            headers=dict(resp.headers),
            cookies=dict(resp.cookies),
            content=resp.content,
            encoding=resp.encoding or "utf-8",
        )

    async def _fetch_curl(self, request: Request, headers: dict[str, str]) -> Response:
        """使用 curl_cffi 请求"""
        impersonate = request.impersonate or self._get_impersonate()
        proxy = request.proxy or self.config.proxy

        kwargs = {
            "method": request.method.value,
            "url": request.url,
            "headers": headers,
            "cookies": request.cookies or None,
            "params": request.params or None,
            "timeout": request.timeout,
            "impersonate": impersonate,
            "allow_redirects": True,
            "verify": self.config.verify_ssl,
        }

        if proxy:
            kwargs["proxy"] = proxy

        if request.data:
            kwargs["data"] = request.data
        if request.json:
            kwargs["json"] = request.json

        # curl_cffi AsyncSession 直接 await
        resp = await self._curl_session.request(**kwargs)

        return Response(
            url=str(resp.url),
            status=resp.status_code,
            headers=dict(resp.headers),
            cookies=dict(resp.cookies),
            content=resp.content,
            encoding=resp.encoding or "utf-8",
        )

    async def get(self, url: str, **kwargs) -> Response:
        """GET 请求"""
        return await self.fetch(Request(url=url, method=RequestMethod.GET, **kwargs))

    async def post(self, url: str, **kwargs) -> Response:
        """POST 请求"""
        return await self.fetch(Request(url=url, method=RequestMethod.POST, **kwargs))

    def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        return {
            **self._stats,
            "use_curl_cffi": self.use_curl,
            "impersonate": self.config.impersonate,
        }
