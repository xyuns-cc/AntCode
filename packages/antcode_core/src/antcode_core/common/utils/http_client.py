"""共享 HTTP 客户端服务

避免每次请求都创建新的 httpx.AsyncClient 实例，
复用连接池提升性能。
"""

import httpx
from loguru import logger


class HttpClientService:
    """HTTP 客户端服务 - 连接池复用"""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._long_timeout_client: httpx.AsyncClient | None = None

    async def start(self):
        """启动客户端"""
        if self._client is None:
            # 默认客户端：10秒超时，适合大多数请求
            self._client = httpx.AsyncClient(
                timeout=10.0,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
            )
            # 长超时客户端：用于文件传输等耗时操作
            self._long_timeout_client = httpx.AsyncClient(
                timeout=300.0,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=10,
                    keepalive_expiry=60.0,
                ),
            )
            logger.info("HTTP 客户端服务已启动")

    async def stop(self):
        """停止客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._long_timeout_client:
            await self._long_timeout_client.aclose()
            self._long_timeout_client = None
        logger.info("HTTP 客户端服务已停止")

    @property
    def client(self) -> httpx.AsyncClient:
        """获取默认客户端"""
        if self._client is None:
            raise RuntimeError("HTTP 客户端未初始化，请先调用 start()")
        return self._client

    @property
    def long_timeout_client(self) -> httpx.AsyncClient:
        """获取长超时客户端"""
        if self._long_timeout_client is None:
            raise RuntimeError("HTTP 客户端未初始化，请先调用 start()")
        return self._long_timeout_client

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """GET 请求"""
        return await self.client.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """POST 请求"""
        return await self.client.post(url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """PUT 请求"""
        return await self.client.put(url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """DELETE 请求"""
        return await self.client.delete(url, **kwargs)


# 全局实例
http_client = HttpClientService()
