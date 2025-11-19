from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Any, Dict, Iterable, Optional, Sequence

from curl_cffi import requests as curl_requests
from DrissionPage import ChromiumOptions, ChromiumPage
from fake_useragent import UserAgent
from scrapy import signals
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.http import HtmlResponse

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 原始模板（保留以兼容 Scrapy 默认结构）
# --------------------------------------------------------------------------- #
class AntcodeSpiderSpiderMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def process_spider_input(self, response, spider):
        return None

    def process_spider_output(self, response, result, spider):
        for item in result:
            yield item

    def process_spider_exception(self, response, exception, spider):
        return None

    async def process_start(self, start):
        async for item_or_request in start:
            yield item_or_request

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s", spider.name)


class AntcodeSpiderDownloaderMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def process_request(self, request, spider):
        return None

    def process_response(self, request, response, spider):
        return response

    def process_exception(self, request, exception, spider):
        return None

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s", spider.name)


# --------------------------------------------------------------------------- #
# 随机桌面端 UA 中间件
# --------------------------------------------------------------------------- #
DEFAULT_USER_AGENT_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class RandomDesktopUserAgentMiddleware:
    MOBILE_KEYWORDS = (
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "windows phone",
    )

    def __init__(
        self,
        browsers: Sequence[str],
        fallback: str = DEFAULT_USER_AGENT_FALLBACK,
        max_attempts: int = 5,
    ) -> None:
        self._browsers = tuple(browsers) or ("chrome", "edge", "firefox", "safari")
        self._fallback = fallback
        self._max_attempts = max(1, max_attempts)
        self._ua: UserAgent | None = None
        self._init_failed_once = False

    @classmethod
    def from_crawler(cls, crawler):
        browsers = crawler.settings.getlist("FAKEUSERAGENT_BROWSERS")
        fallback = crawler.settings.get("FAKEUSERAGENT_FALLBACK", DEFAULT_USER_AGENT_FALLBACK)
        max_attempts = crawler.settings.getint("FAKEUSERAGENT_MAX_ATTEMPTS", 5)
        middleware = cls(
            browsers=browsers or ("chrome", "edge", "firefox", "safari"),
            fallback=fallback,
            max_attempts=max_attempts,
        )
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def spider_opened(self, spider):
        spider.logger.info("桌面端 UA 池启用：%s", ", ".join(self._browsers))

    def _ensure_user_agent_pool(self, spider) -> UserAgent | None:
        if self._ua is None:
            try:
                self._ua = UserAgent(browsers=list(self._browsers))
                self._init_failed_once = False
            except Exception as exc:  # noqa: BLE001
                if not self._init_failed_once:
                    spider.logger.warning("初始化 fake-useragent 失败，使用备用 UA：%s", exc)
                self._init_failed_once = True
                self._ua = None
        return self._ua

    def _is_desktop(self, user_agent: str) -> bool:
        ua_lower = user_agent.lower()
        return not any(marker in ua_lower for marker in self.MOBILE_KEYWORDS)

    def _get_random_user_agent(self, spider) -> str:
        ua = self._ensure_user_agent_pool(spider)
        if ua is not None:
            for _ in range(self._max_attempts):
                try:
                    candidate = ua.random
                except Exception as exc:  # noqa: BLE001
                    if not self._init_failed_once:
                        spider.logger.warning("获取随机 UA 失败，使用备用 UA：%s", exc)
                    self._init_failed_once = True
                    break
                if self._is_desktop(candidate):
                    return candidate
        return self._fallback

    def process_request(self, request, spider):
        request.headers["User-Agent"] = self._get_random_user_agent(spider)
        return None


# --------------------------------------------------------------------------- #
# DrissionPage 无头浏览器中间件
# --------------------------------------------------------------------------- #
class DrissionPageMiddleware:
    def __init__(self, crawler):
        settings = crawler.settings
        self.enabled = settings.getbool("DRISSIONPAGE_ENABLED", True)
        if not self.enabled:
            raise NotConfigured("DrissionPage middleware is disabled")

        self.headless = settings.getbool("DRISSIONPAGE_HEADLESS", True)
        self.window_size = settings.get("DRISSIONPAGE_WINDOW_SIZE", "1920,1080")
        self.default_wait_time = settings.getint("DRISSIONPAGE_WAIT_TIME", 3)
        self.page_load_timeout = settings.getint("DRISSIONPAGE_PAGE_LOAD_TIMEOUT", 30)
        self.retry_on_failure = settings.getint("DRISSIONPAGE_RETRY", 0)
        self.browser_path = settings.get("DRISSIONPAGE_BROWSER_PATH")
        self.user_data_path = settings.get("DRISSIONPAGE_USER_DATA_PATH")

        self.chrome_arguments = settings.getlist(
            "DRISSIONPAGE_ARGUMENTS",
            [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-logging",
                "--ignore-certificate-errors",
            ],
        )

        self._page_lock = Lock()
        self._page: Optional[ChromiumPage] = None
        self._current_proxy: Optional[str] = None

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls(crawler)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    def spider_closed(self, spider):
        with self._page_lock:
            if self._page:
                try:
                    self._page.quit()
                    logger.info("DrissionPage 浏览器已关闭")
                except Exception as exc:  # noqa: BLE001
                    logger.error("关闭 DrissionPage 浏览器失败: %s", exc)
                finally:
                    self._page = None

    def process_request(self, request, spider):
        if not self._should_use_browser(request):
            return None

        if request.method.upper() != "GET":
            logger.error("DrissionPage 仅支持 GET 请求，任务被丢弃: %s", request.url)
            raise IgnoreRequest("DrissionPage only supports GET requests.")

        proxy = request.meta.get("proxy")
        try:
            page = self._ensure_page(proxy)
            wait_time = request.meta.get("wait_time", self.default_wait_time)
            return self._render_with_browser(request, page, wait_time)
        except IgnoreRequest:
            raise
        except Exception as exc:  # noqa: BLE001
            retry = request.meta.get("drissionpage_retry", 0)
            if retry < self.retry_on_failure:
                request.meta["drissionpage_retry"] = retry + 1
                logger.warning(
                    "DrissionPage 请求失败，将重试（%s/%s）%s -> %s",
                    retry + 1,
                    self.retry_on_failure,
                    request.url,
                    exc,
                )
                return request
            logger.error("DrissionPage 请求失败，任务被丢弃: %s -> %s", request.url, exc)
            raise IgnoreRequest(f"DrissionPage request failed: {exc}") from exc

    def _should_use_browser(self, request) -> bool:
        return bool(
            request.meta.get("fetch_type") == "browser"
            or request.meta.get("browser_request")
        )

    def _ensure_page(self, proxy: Optional[str]) -> ChromiumPage:
        with self._page_lock:
            proxy_changed = proxy != self._current_proxy
            if self._page and proxy_changed:
                try:
                    self._page.quit()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("关闭旧浏览器实例失败: %s", exc)
                finally:
                    self._page = None

            if self._page is None:
                options = self._create_browser_options(proxy)
                self._page = ChromiumPage(addr_or_opts=options)
                self._page.set.timeouts(page_load=self.page_load_timeout)
                self._current_proxy = proxy

            return self._page

    def _create_browser_options(self, proxy: Optional[str]) -> ChromiumOptions:
        options = ChromiumOptions()
        if self.browser_path:
            options.set_browser_path(self.browser_path)
        if self.user_data_path:
            options.set_user_data_path(self.user_data_path)

        if self.headless:
            options.headless()
            options.set_argument("--headless=new")

        width, height = self.window_size.split(",")
        options.set_window_size(int(width), int(height))

        for arg in self.chrome_arguments:
            options.set_argument(arg)
        options.set_argument("--disable-gpu")
        options.set_argument("--disable-software-rasterizer")

        if proxy:
            proxy_server = self._normalize_proxy(proxy)
            if proxy_server:
                options.set_argument(f"--proxy-server={proxy_server}")
                logger.info("DrissionPage 设置代理: %s", proxy_server)

        return options

    def _render_with_browser(self, request, page: ChromiumPage, wait_time: int):
        logger.info("DrissionPage GET: %s", request.url)
        headers = self._normalize_headers(request.headers)
        if headers:
            page.set.headers(headers)

        page.get(request.url)

        if wait_time > 0:
            time.sleep(wait_time)

        wait_selector = request.meta.get("wait_selector")
        if wait_selector:
            self._wait_for_element(page, wait_selector, request.meta)

        pagination = request.meta.get("pagination") or {}
        if pagination.get("method") == "click_element":
            self._handle_click_pagination(page, request.meta, pagination)

        self._click_elements(page, request.meta.get("click_selectors", []))
        self._execute_js(page, request.meta.get("execute_script"))

        if request.meta.get("scroll_to_bottom"):
            self._scroll_to_bottom(page)

        html = page.html
        extra_data = self._collect_extra_data(page, request.meta)

        response = HtmlResponse(
            url=page.url,
            body=html.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )
        if extra_data:
            response.meta["extra_data"] = extra_data
        return response

    def _handle_click_pagination(self, page: ChromiumPage, meta: Dict, pagination: Dict):
        current_page = meta.get("current_page", 1)
        if current_page <= 1:
            return

        rule = pagination.get("next_page_rule") or {}
        for _ in range(1, current_page):
            if not self._click_next_page(page, rule, pagination):
                break

    def _click_next_page(self, page: ChromiumPage, rule: Dict, pagination: Dict) -> bool:
        selector = self._normalize_selector(rule)
        if not selector:
            return False
        try:
            element = page.ele(selector, timeout=pagination.get("wait_element_timeout", 5))
            if element:
                element.click()
                time.sleep(pagination.get("wait_after_click_ms", 2500) / 1000)
                return True
            logger.warning("DrissionPage 未找到下一页元素: %s", selector)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("DrissionPage 点击下一页失败: %s", exc)
            return False

    def _click_elements(self, page: ChromiumPage, selectors: Iterable):
        if not selectors:
            return
        if isinstance(selectors, str):
            selectors = [selectors]
        for selector in selectors:
            normalized = self._normalize_selector({"expr": selector})
            if not normalized:
                continue
            try:
                element = page.ele(normalized, timeout=3)
                if element:
                    element.click()
                    time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DrissionPage 点击元素失败 %s: %s", normalized, exc)

    def _execute_js(self, page: ChromiumPage, script: Optional[str]):
        if not script:
            return
        try:
            page.run_js(script)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DrissionPage 执行自定义 JS 失败: %s", exc)

    def _scroll_to_bottom(self, page: ChromiumPage):
        try:
            page.scroll.to_bottom()
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DrissionPage 滚动页面失败: %s", exc)

    def _collect_extra_data(self, page: ChromiumPage, meta: Dict) -> Dict:
        extra = {}
        if meta.get("extract_cookies"):
            extra["cookies"] = page.cookies(as_dict=True)
        if meta.get("extract_title"):
            extra["title"] = page.title
        if meta.get("extract_url"):
            extra["current_url"] = page.url
        return extra

    def _wait_for_element(self, page: ChromiumPage, selector: str, meta: Dict):
        selector = self._normalize_selector({"expr": selector})
        if not selector:
            return
        wait_type = meta.get("wait_type", "loaded")
        timeout = meta.get("max_wait_time", self.page_load_timeout)
        try:
            wait = page.wait
            if wait_type == "displayed":
                wait.ele_displayed(selector, timeout=timeout)
            elif wait_type == "enabled":
                wait.ele_enabled(selector, timeout=timeout)
            elif wait_type == "clickable":
                wait.ele_clickable(selector, timeout=timeout)
            else:
                wait.ele_loaded(selector, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DrissionPage 等待元素失败 %s: %s", selector, exc)

    @staticmethod
    def _normalize_headers(headers) -> Dict[str, str]:
        normalized = {}
        for key, value in headers.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            if isinstance(value, (list, tuple)):
                value = value[0]
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            normalized[str(key)] = str(value)
        return normalized

    @staticmethod
    def _normalize_proxy(proxy: str) -> Optional[str]:
        if not proxy:
            return None
        if "://" not in proxy:
            return f"http://{proxy}"
        return proxy

    @staticmethod
    def _normalize_selector(rule: Dict) -> Optional[str]:
        if not rule:
            return None
        expr = rule.get("expr") or rule.get("selector") or rule.get("expr_text")
        if not expr:
            return None
        return f"css:{expr}" if rule.get("type") == "css" else expr


# --------------------------------------------------------------------------- #
# curl_cffi 中间件
# --------------------------------------------------------------------------- #
class CurlCffiMiddleware:
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
        """curl_cffi 无需手动清理资源。"""

    def process_request(self, request, spider):
        if not request.meta.get("curl_cffi_request"):
            return None

        method = request.method.upper()
        if method not in self.SUPPORTED_METHODS:
            logger.error("curl_cffi 不支持的方法 %s，任务被丢弃: %s", method, request.url)
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
            retry = request.meta.get("curl_cffi_retry", 0)
            if retry < self.max_retries:
                request.meta["curl_cffi_retry"] = retry + 1
                logger.warning(
                    "curl_cffi 请求失败，将重试（%s/%s）%s -> %s",
                    retry + 1,
                    self.max_retries,
                    request.url,
                    exc,
                )
                return request
            logger.error("curl_cffi 请求失败，任务被丢弃: %s -> %s", request.url, exc)
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

    def _collect_options(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        options = meta.get("curl_cffi_options") or {}
        impersonate = options.get("impersonate", self.default_impersonate)
        timeout = options.get("timeout", self.default_timeout)
        verify = options.get("verify", self.default_verify)
        allow_redirects = options.get("allow_redirects", self.default_allow_redirects)
        http2 = options.get("http2", False)
        as_json = options.get("as_json", True)

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
        return str(body).encode("utf-8"), None

    @staticmethod
    def _extract_headers(request) -> Dict[str, str]:
        headers = {}
        for key, value in request.headers.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", errors="ignore")
            if isinstance(value, (list, tuple)):
                value = value[0]
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            headers[str(key)] = str(value)
        return headers

    @staticmethod
    def _extract_cookies(request) -> Dict[str, str]:
        cookies = {}
        if isinstance(request.cookies, dict):
            for key, value in request.cookies.items():
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="ignore")
                cookies[str(key)] = str(value)
        return cookies

    @staticmethod
    def _extract_proxies(request) -> Optional[Dict[str, str]]:
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
# Define here the models for your spider middleware
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/spider-middleware.html

from scrapy import signals

from __future__ import annotations

from typing import Sequence

from fake_useragent import UserAgent


class AntcodeSpiderSpiderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the spider middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response, spider):
        # Called for each response that goes through the spider
        # middleware and into the spider.

        # Should return None or raise an exception.
        return None

    def process_spider_output(self, response, result, spider):
        # Called with the results returned from the Spider, after
        # it has processed the response.

        # Must return an iterable of Request, or item objects.
        for i in result:
            yield i

    def process_spider_exception(self, response, exception, spider):
        # Called when a spider or process_spider_input() method
        # (from other spider middleware) raises an exception.

        # Should return either None or an iterable of Request or item objects.
        pass

    async def process_start(self, start):
        # Called with an async iterator over the spider start() method or the
        # maching method of an earlier spider middleware.
        async for item_or_request in start:
            yield item_or_request

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


class AntcodeSpiderDownloaderMiddleware:
    # Not all methods need to be defined. If a method is not defined,
    # scrapy acts as if the downloader middleware does not modify the
    # passed objects.

    @classmethod
    def from_crawler(cls, crawler):
        # This method is used by Scrapy to create your spiders.
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        # Called for each request that goes through the downloader
        # middleware.

        # Must either:
        # - return None: continue processing this request
        # - or return a Response object
        # - or return a Request object
        # - or raise IgnoreRequest: process_exception() methods of
        #   installed downloader middleware will be called
        return None

    def process_response(self, request, response, spider):
        # Called with the response returned from the downloader.

        # Must either;
        # - return a Response object
        # - return a Request object
        # - or raise IgnoreRequest
        return response

    def process_exception(self, request, exception, spider):
        # Called when a download handler or a process_request()
        # (from other downloader middleware) raises an exception.

        # Must either:
        # - return None: continue processing this exception
        # - return a Response object: stops process_exception() chain
        # - return a Request object: stops process_exception() chain
        pass

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s" % spider.name)


DEFAULT_USER_AGENT_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class RandomDesktopUserAgentMiddleware:
    """为每个请求注入桌面端随机 User-Agent。"""

    MOBILE_KEYWORDS = (
        "android",
        "iphone",
        "ipad",
        "ipod",
        "mobile",
        "windows phone",
    )

    def __init__(
        self,
        browsers: Sequence[str],
        fallback: str = DEFAULT_USER_AGENT_FALLBACK,
        max_attempts: int = 5,
    ) -> None:
        self._browsers = tuple(browsers) or ("chrome", "edge", "firefox", "safari")
        self._fallback = fallback
        self._max_attempts = max(1, max_attempts)
        self._ua: UserAgent | None = None
        self._init_failed_once = False

    @classmethod
    def from_crawler(cls, crawler):
        browsers = crawler.settings.getlist("FAKEUSERAGENT_BROWSERS")
        fallback = crawler.settings.get(
            "FAKEUSERAGENT_FALLBACK",
            DEFAULT_USER_AGENT_FALLBACK,
        )
        max_attempts = crawler.settings.getint("FAKEUSERAGENT_MAX_ATTEMPTS", 5)
        middleware = cls(
            browsers=browsers or ("chrome", "edge", "firefox", "safari"),
            fallback=fallback,
            max_attempts=max_attempts,
        )
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def spider_opened(self, spider):
        spider.logger.info(
            "已启用桌面端 User-Agent 池，候选浏览器：%s",
            ", ".join(self._browsers),
        )

    def _ensure_user_agent_pool(self, spider) -> UserAgent | None:
        if self._ua is None:
            try:
                self._ua = UserAgent(browsers=list(self._browsers))
                self._init_failed_once = False
            except Exception as exc:  # noqa: BLE001
                if not self._init_failed_once:
                    spider.logger.warning(
                        "初始化 fake-useragent 失败，将使用备用 User-Agent。错误：%s",
                        exc,
                    )
                self._init_failed_once = True
                self._ua = None
        return self._ua

    def _is_desktop(self, user_agent: str) -> bool:
        ua_lower = user_agent.lower()
        return not any(marker in ua_lower for marker in self.MOBILE_KEYWORDS)

    def _get_random_user_agent(self, spider) -> str:
        ua = self._ensure_user_agent_pool(spider)
        if ua is not None:
            for _ in range(self._max_attempts):
                try:
                    candidate = ua.random
                except Exception as exc:  # noqa: BLE001
                    if not self._init_failed_once:
                        spider.logger.warning(
                            "从 fake-useragent 获取随机 User-Agent 失败，将使用备用值。错误：%s",
                            exc,
                        )
                    self._init_failed_once = True
                    break
                if self._is_desktop(candidate):
                    return candidate
        return self._fallback

    def process_request(self, request, spider):
        request.headers["User-Agent"] = self._get_random_user_agent(spider)
        return None
