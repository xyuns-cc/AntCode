"""请求响应对象"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union
from enum import Enum


class RequestMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


@dataclass
class Request:
    """HTTP 请求对象"""
    url: str
    method: RequestMethod = RequestMethod.GET
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    data: Optional[Union[Dict, str, bytes]] = None
    json: Optional[Dict] = None

    # 回调
    callback: Optional[Callable] = None
    errback: Optional[Callable] = None
    cb_kwargs: Dict[str, Any] = field(default_factory=dict)

    # 元数据
    meta: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    dont_filter: bool = False

    # 重试
    max_retries: int = 3
    retry_count: int = 0

    # 超时
    timeout: float = 30.0

    # 代理
    proxy: Optional[str] = None

    # 指纹伪装
    impersonate: Optional[str] = None  # chrome, firefox, safari 等

    def __post_init__(self):
        if isinstance(self.method, str):
            self.method = RequestMethod(self.method.upper())

    def copy(self) -> "Request":
        """复制请求"""
        return Request(
            url=self.url,
            method=self.method,
            headers=self.headers.copy(),
            cookies=self.cookies.copy(),
            params=self.params.copy(),
            data=self.data,
            json=self.json,
            callback=self.callback,
            errback=self.errback,
            cb_kwargs=self.cb_kwargs.copy(),
            meta=self.meta.copy(),
            priority=self.priority,
            dont_filter=self.dont_filter,
            max_retries=self.max_retries,
            retry_count=self.retry_count,
            timeout=self.timeout,
            proxy=self.proxy,
            impersonate=self.impersonate,
        )

    def replace(self, **kwargs) -> "Request":
        """替换属性"""
        req = self.copy()
        for key, value in kwargs.items():
            if hasattr(req, key):
                setattr(req, key, value)
        return req


@dataclass
class Response:
    """HTTP 响应对象"""
    url: str
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    encoding: str = "utf-8"

    # 关联请求
    request: Optional[Request] = None

    # 元数据
    meta: Dict[str, Any] = field(default_factory=dict)

    # 时间
    elapsed_ms: float = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def text(self) -> str:
        """文本内容"""
        try:
            return self.content.decode(self.encoding)
        except UnicodeDecodeError:
            return self.content.decode("utf-8", errors="ignore")

    @property
    def ok(self) -> bool:
        """是否成功"""
        return 200 <= self.status < 400

    def json(self) -> Any:
        """JSON 解析"""
        import ujson
        return ujson.loads(self.text)

    def xpath(self, query: str):
        """XPath 选择器"""
        from .selector import Selector
        return Selector(self.text).xpath(query)

    def css(self, query: str):
        """CSS 选择器"""
        from .selector import Selector
        return Selector(self.text).css(query)

    def re(self, pattern: str, flags: int = 0) -> List[str]:
        """正则匹配"""
        from .selector import Selector
        return Selector(self.text).re(pattern, flags)

    def re_first(self, pattern: str, default: str = None, flags: int = 0) -> Optional[str]:
        """正则匹配第一个"""
        from .selector import Selector
        return Selector(self.text).re_first(pattern, default, flags)
