from __future__ import annotations

import time
from threading import Lock
from typing import Dict, Iterable, Optional

from DrissionPage import ChromiumOptions, ChromiumPage
from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse

from spider.spider.utils.log import logger


class DrissionPageMiddleware:
    """使用最新版 DrissionPage 的下载中间件，默认强制无头运行。"""

    def __init__(self, crawler):
        self.crawler = crawler
        settings = crawler.settings
        self.enabled = settings.getbool("DRISSIONPAGE_ENABLED", True)
        if not self.enabled:
            raise NotConfigured("DrissionPage middleware is disabled")

        # 基础配置
        self.headless = settings.getbool("DRISSIONPAGE_HEADLESS", True)
        self.window_size = settings.get("DRISSIONPAGE_WINDOW_SIZE", "1920,1080")
        self.default_wait_time = settings.getint("DRISSIONPAGE_WAIT_TIME", 3)
        self.page_load_timeout = settings.getint("DRISSIONPAGE_PAGE_LOAD_TIMEOUT", 30)
        self.retry_on_failure = settings.getint("DRISSIONPAGE_RETRY", 0)
        self.binary_path = settings.get("DRISSIONPAGE_BROWSER_PATH")
        self.user_data_path = settings.get("DRISSIONPAGE_USER_DATA_PATH")

        default_arguments = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--disable-plugins",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-infobars",
            "--disable-notifications",
            "--disable-logging",
            "--ignore-certificate-errors",
        ]
        self.chrome_arguments = settings.getlist("DRISSIONPAGE_ARGUMENTS", default_arguments)

        # 初始化共享浏览器实例
        self._page_lock = Lock()
        self._page: Optional[ChromiumPage] = None
        self._current_proxy: Optional[str] = None

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls(crawler)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        return middleware

    def _create_browser_options(self, proxy: Optional[str] = None) -> ChromiumOptions:
        options = ChromiumOptions()
        if self.binary_path:
            options.set_browser_path(self.binary_path)
        if self.user_data_path:
            options.set_user_data_path(self.user_data_path)

        if self.headless:
            options.headless()  # DrissionPage 自动适配新版无头模式
            options.set_argument("--headless=new")

        width, height = self.window_size.split(",")
        options.set_window_size(int(width), int(height))

        for arg in self.chrome_arguments:
            options.set_argument(arg)
        options.set_argument("--disable-gpu")
        options.set_argument("--disable-software-rasterizer")

        if proxy:
            proxy_server = self._parse_proxy(proxy)
            if proxy_server:
                options.set_argument(f"--proxy-server={proxy_server}")
                logger.info(f"DrissionPage 设置代理: {proxy_server}")

        return options

    def _ensure_page(self, proxy: Optional[str]) -> ChromiumPage:
        with self._page_lock:
            proxy_changed = proxy != self._current_proxy
            if self._page and proxy_changed:
                try:
                    self._page.quit()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"关闭旧浏览器实例失败: {exc}")
                finally:
                    self._page = None

            if self._page is None:
                options = self._create_browser_options(proxy)
                self._page = ChromiumPage(addr_or_opts=options)
                self._page.set.timeouts(page_load=self.page_load_timeout)
                self._current_proxy = proxy

            return self._page

    def process_request(self, request, spider):
        if not self._should_use_browser(request):
            return None

        if request.method.upper() != "GET":
            logger.warning("DrissionPage 仅支持 GET 请求，当前方法: %s", request.method)
            return None

        proxy = request.meta.get("proxy")
        try:
            page = self._ensure_page(proxy)
            wait_time = request.meta.get("wait_time", self.default_wait_time)
            return self._render_with_browser(request, page, wait_time)
        except Exception as exc:  # noqa: BLE001
            logger.error("DrissionPage 处理请求失败 [%s]: %s", request.url, exc)
            if self.retry_on_failure > 0:
                request.meta["drissionpage_retry"] = request.meta.get("drissionpage_retry", 0) + 1
                if request.meta["drissionpage_retry"] <= self.retry_on_failure:
                    logger.info("DrissionPage 重试第 %s 次: %s", request.meta["drissionpage_retry"], request.url)
                    return request
            return None

    def _should_use_browser(self, request) -> bool:
        return request.meta.get("fetch_type") == "browser" or request.meta.get("browser_request")

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
        self._execute_custom_js(page, request.meta.get("execute_script"))
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
        logger.info("DrissionPage 点击分页，目标页码: %s", current_page)
        if current_page <= 1:
            return

        next_page_rule = pagination.get("next_page_rule") or {}
        for _ in range(1, current_page):
            success = self._click_next_page(page, next_page_rule, pagination)
            if not success:
                break

    def _click_next_page(self, page: ChromiumPage, next_page_rule: Dict, pagination: Dict) -> bool:
        selector = self._build_selector(next_page_rule)
        if not selector:
            return False
        try:
            element = page.ele(selector, timeout=pagination.get("wait_element_timeout", 5))
            if element:
                element.click()
                wait_ms = pagination.get("wait_after_click_ms", 2500)
                time.sleep(wait_ms / 1000)
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
            normalized = self._build_selector({"expr": selector})
            if not normalized:
                continue
            try:
                element = page.ele(normalized, timeout=3)
                if element:
                    element.click()
                    time.sleep(0.3)
                else:
                    logger.debug("DrissionPage 未找到点击元素: %s", normalized)
            except Exception as exc:  # noqa: BLE001
                logger.warning("DrissionPage 点击元素失败 %s: %s", normalized, exc)

    def _execute_custom_js(self, page: ChromiumPage, script: Optional[str]):
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
        wait_type = meta.get("wait_type", "loaded")
        max_wait = meta.get("max_wait_time", self.page_load_timeout)
        selector = self._build_selector({"expr": selector})
        if not selector:
            return
        try:
            wait = page.wait
            if wait_type == "displayed":
                wait.ele_displayed(selector, timeout=max_wait)
            elif wait_type == "enabled":
                wait.ele_enabled(selector, timeout=max_wait)
            elif wait_type == "clickable":
                wait.ele_clickable(selector, timeout=max_wait)
            else:
                wait.ele_loaded(selector, timeout=max_wait)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DrissionPage 等待元素失败 %s: %s", selector, exc)

    def _normalize_headers(self, headers) -> Dict[str, str]:
        if not headers:
            return {}
        normalized = {}
        for key, value in headers.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if isinstance(value, (list, tuple)):
                value = value[0]
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            normalized[str(key)] = str(value)
        return normalized

    def _parse_proxy(self, proxy_url: Optional[str]) -> Optional[str]:
        if not proxy_url:
            return None
        try:
            if "://" not in proxy_url:
                return f"http://{proxy_url}"
            return proxy_url
        except Exception as exc:  # noqa: BLE001
            logger.warning("DrissionPage 解析代理失败 %s: %s", proxy_url, exc)
            return None

    def _build_selector(self, rule: Dict) -> Optional[str]:
        if not rule:
            return None
        expr = rule.get("expr") or rule.get("selector") or rule.get("expr_text")
        if not expr:
            return None
        selector_type = rule.get("type")
        if selector_type == "css":
            return f"css:{expr}"
        return expr

    def spider_closed(self, spider):
        with self._page_lock:
            if self._page:
                try:
                    self._page.quit()
                    logger.info("DrissionPage 浏览器已关闭")
                except Exception as exc:  # noqa: BLE001
                    logger.error("DrissionPage 关闭浏览器失败: %s", exc)
                finally:
                    self._page = None
        
        if not expr:
            logger.error("下一页规则表达式为空")
            return False
        
        try:
            # 根据规则类型构建选择器
            if rule_type == 'xpath':
                selector = expr
            elif rule_type == 'css':
                selector = f'css:{expr}'
            else:
                logger.error(f"不支持的选择器类型: {rule_type}")
                return False
            
            # 查找并点击元素
            element = page.ele(selector)
            if element:
                element.click()
                logger.debug(f"成功点击下一页按钮: {selector}")
                
                # 等待页面加载
                wait_ms = pagination.get('wait_after_click_ms', 2500)
                time.sleep(wait_ms / 1000)
                return True
            else:
                logger.warning(f"未找到下一页按钮: {selector}")
                return False
                
        except Exception as e:
            logger.error(f"点击下一页失败: {e}")
            return False
    
    def _click_elements(self, page, selectors):
        """点击指定的元素"""
        if isinstance(selectors, str):
            selectors = [selectors]
        
        for selector in selectors:
            try:
                # 使用原生方式查找元素
                element = page.ele(selector)
                if element:
                    element.click()
                    logger.debug(f"成功点击元素: {selector}")
                    time.sleep(0.5)  # 点击后短暂等待
                else:
                    logger.warning(f"未找到要点击的元素: {selector}")
            except Exception as e:
                logger.warning(f"点击元素失败 {selector}: {e}")
    
    def _wait_for_element(self, page, selector, meta):
        """等待特定元素出现"""
        max_wait = meta.get('max_wait_time', 10)
        wait_type = meta.get('wait_type', 'loaded')  # loaded, displayed, enabled, clickable
        
        try:
            if wait_type == 'loaded':
                # 等待元素加载
                page.wait.ele_loaded(selector, timeout=max_wait)
            elif wait_type == 'displayed':
                # 等待元素显示
                page.wait.ele_displayed(selector, timeout=max_wait)
            elif wait_type == 'enabled':
                # 等待元素可用
                page.wait.ele_enabled(selector, timeout=max_wait)
            elif wait_type == 'clickable':
                # 等待元素可点击
                page.wait.ele_clickable(selector, timeout=max_wait)
            else:
                # 默认等待元素加载
                page.wait.ele_loaded(selector, timeout=max_wait)
                
            logger.debug(f"成功等待元素 {selector} ({wait_type})")
            
        except Exception as e:
            logger.warning(f"等待元素失败 {selector}: {e}")
    
    def spider_closed(self, spider):
        """爬虫关闭时清理资源"""
        if self.page:
            try:
                self.page.quit()
                logger.info("DrissionPage浏览器已关闭")
            except Exception as e:
                logger.error(f"关闭DrissionPage浏览器失败: {e}")