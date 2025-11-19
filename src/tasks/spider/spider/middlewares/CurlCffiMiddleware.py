from __future__ import annotations

from typing import Any, Dict, Optional

from curl_cffi import requests as curl_requests
from scrapy import signals
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import HtmlResponse

from spider.spider.utils.log import logger


class CurlCffiMiddleware:
    """基于 curl_cffi 的下载中间件，支持模拟浏览器指纹的 HTTP 请求。"""

    SUPPORTED_METHODS = {"GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"}

    def __init__(self, crawler):
        settings = crawler.settings
        self.enabled = settings.getbool("CURL_CFFI_ENABLED", True)
        if not self.enabled:
            raise NotConfigured("CurlCffi middleware is disabled")

        self.default_impersonate = settings.get("CURL_CFFI_IMPERSONATE", "chrome120")
        self.default_timeout = settings.getint("CURL_CFFI_TIMEOUT", 30)
        self.default_verify = settings.getbool("CURL_CFFI_VERIFY", True)
        self.default_allow_redirects = settings.getbool("CURL_CFFI_ALLOW_REDIRECTS", True)
        self.max_retries = settings.getint("CURL_CFFI_RETRY", 0)

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls(crawler)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    def spider_closed(self, spider):
        """占位，curl_cffi 无需额外清理。"""

    # ------------------------------------------------------------------ #
    # Scrapy Hook
    # ------------------------------------------------------------------ #
    def process_request(self, request, spider):
        if not request.meta.get("curl_cffi_request"):
            return None

        method = request.method.upper()
        if method not in self.SUPPORTED_METHODS:
            logger.warning("curl_cffi 不支持的 HTTP 方法 %s，任务被忽略: %s", method, request.url)
            raise IgnoreRequest(f"Unsupported method for curl_cffi: {method}")

        options = self._collect_options(request.meta)

        body, json_data = self._extract_body(request)
        headers = self._extract_headers(request)
        cookies = self._extract_cookies(request)
        proxies = self._extract_proxies(request)

        try:
            response = curl_requests.request(
                method=method,
                url=request.url,
                headers=headers or None,
                data=body,
                json=json_data,
                cookies=cookies or None,
                proxies=proxies,
                impersonate=options["impersonate"],
                timeout=options["timeout"],
                verify=options["verify"],
                allow_redirects=options["allow_redirects"],
                http2=options["http2"],
            )
        except Exception as exc:  # noqa: BLE001
            retry_count = request.meta.get("curl_cffi_retry", 0)
            if retry_count < self.max_retries:
                request.meta["curl_cffi_retry"] = retry_count + 1
                logger.warning(
                    "curl_cffi 请求失败，准备重试 (%s/%s): %s，错误: %s",
                    retry_count + 1,
                    self.max_retries,
                    request.url,
                    exc,
                )
                return request

            logger.error("curl_cffi 请求失败，任务被丢弃: %s，错误: %s", request.url, exc)
            raise IgnoreRequest(f"curl_cffi request failed: {exc}") from exc

        logger.info(
            "curl_cffi 请求完成: %s [%s] (impersonate=%s)",
            request.url,
            response.status_code,
            options["impersonate"],
        )

        return HtmlResponse(
            url=response.url,
            status=response.status_code,
            headers=response.headers,
            body=response.content,
            encoding=response.encoding or "utf-8",
            request=request,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _collect_options(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        options = meta.get("curl_cffi_options", {}) or {}

        impersonate = options.get("impersonate", self.default_impersonate)
        timeout = options.get("timeout", self.default_timeout)
        verify = options.get("verify", self.default_verify)
        allow_redirects = options.get("allow_redirects", self.default_allow_redirects)
        http2 = options.get("http2", False)
        as_json = options.get("as_json", True)

        # 持久写回，以便后续中间件使用
        meta["curl_cffi_options"] = {
            "impersonate": impersonate,
            "timeout": timeout,
            "verify": verify,
            "allow_redirects": allow_redirects,
            "http2": http2,
            "as_json": as_json,
        }

        return meta["curl_cffi_options"]

    def _extract_body(self, request) -> tuple[Optional[Any], Optional[Any]]:
        """根据请求信息决定以 data 还是 json 形式提交。"""
        body = request.body or request.meta.get("request_body")
        if body is None:
            return None, None

        as_json = request.meta.get("curl_cffi_options", {}).get("as_json", True)

        if isinstance(body, (bytes, bytearray)):
            return body, None
        if isinstance(body, dict):
            return (None, body) if as_json else (self._encode_dict(body), None)
        if isinstance(body, str):
            return body.encode("utf-8"), None

        # 其他类型直接转字符串
        return str(body).encode("utf-8"), None

    def _extract_headers(self, request) -> Dict[str, str]:
        headers = {}
        for key, value in request.headers.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            headers[str(key)] = str(value)
        return headers

    def _extract_cookies(self, request) -> Dict[str, str]:
        cookies = {}
        if isinstance(request.cookies, dict):
            for key, value in request.cookies.items():
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                cookies[str(key)] = str(value)
        return cookies

    def _extract_proxies(self, request) -> Optional[Dict[str, str]]:
        proxy = request.meta.get("proxy")
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    @staticmethod
    def _encode_dict(data: Dict[str, Any]) -> Dict[str, str]:
        encoded = {}
        for key, value in data.items():
            key = str(key)
            if value is None:
                encoded[key] = ""
            elif isinstance(value, (list, tuple)):
                encoded[key] = ",".join(str(item) for item in value)
            else:
                encoded[key] = str(value)
        return encoded

