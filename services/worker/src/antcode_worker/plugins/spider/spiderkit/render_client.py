"""
DrissionPage 渲染客户端 - 浏览器多开管理

特性:
- 浏览器实例池，支持多开
- 用户数据持久化到 data 目录
- 自动端口分配
- headless/GUI 模式切换
- 代理支持
- 页面超时和重试
"""

import asyncio
import contextlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# DrissionPage 导入
try:
    from DrissionPage import Chromium, ChromiumOptions
    from DrissionPage.common import Settings as DPSettings

    HAS_DRISSIONPAGE = True
except ImportError:
    HAS_DRISSIONPAGE = False
    Chromium = None
    ChromiumOptions = None
    DPSettings = None


@dataclass
class RenderConfig:
    """渲染客户端配置"""

    # 浏览器池
    max_browsers: int = 3
    max_tabs_per_browser: int = 5

    # 数据目录
    data_dir: str = "data/browsers"

    # 浏览器设置
    headless: bool = True
    browser_path: str | None = None

    # 端口范围
    port_start: int = 9200
    port_end: int = 9300

    # 超时
    page_load_timeout: int = 30
    script_timeout: int = 30
    base_timeout: int = 10

    # 重试
    retry_times: int = 3
    retry_interval: float = 2.0

    # 代理
    proxy: str | None = None

    # 窗口
    window_size: str = "1920,1080"

    # 页面加载策略: normal(完全加载), eager(DOM就绪), none(连接成功即停止)
    load_mode: str = "normal"

    # 浏览器行为
    no_imgs: bool = False  # 禁用图片加载
    no_js: bool = False  # 禁用 JavaScript
    mute: bool = True  # 静音
    incognito: bool = False  # 匿名/隐私模式
    new_env: bool = False  # 每次启动新环境（不复用用户数据）
    ignore_certificate_errors: bool = True  # 忽略证书错误

    # User-Agent
    user_agent: str | None = None

    # 下载目录
    download_path: str | None = None

    # 自定义命令行参数
    extra_arguments: list[str] = field(default_factory=list)

    # 自定义偏好设置
    preferences: dict[str, Any] = field(default_factory=dict)

    # 扩展插件路径列表
    extensions: list[str] = field(default_factory=list)

    # 实验性 flags
    flags: dict[str, str] = field(default_factory=dict)


@dataclass
class BrowserInstance:
    """浏览器实例"""

    browser_id: str
    port: int
    user_data_path: str
    browser: Any = None  # Chromium 对象
    tabs: list[Any] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    is_busy: bool = False

    @property
    def tab_count(self) -> int:
        return len(self.tabs) if self.tabs else 0


class BrowserPool:
    """
    浏览器实例池

    管理多个浏览器实例，支持:
    - 自动创建和销毁
    - 实例复用
    - 用户数据持久化
    """

    def __init__(self, config: RenderConfig | None = None):
        self.config = config or RenderConfig()
        self._instances: dict[str, BrowserInstance] = {}
        self._port_map: dict[int, str] = {}  # port -> browser_id
        self._lock = asyncio.Lock()
        self._running = False

        # 确保数据目录存在
        self._data_path = Path(self.config.data_dir)
        self._data_path.mkdir(parents=True, exist_ok=True)

        # 统计
        self._stats = {
            "browsers_created": 0,
            "browsers_closed": 0,
            "pages_loaded": 0,
            "errors": 0,
        }

    def _find_available_port(self) -> int:
        """查找可用端口"""
        for port in range(self.config.port_start, self.config.port_end):
            if port not in self._port_map:
                return port
        raise RuntimeError(
            f"无可用端口 ({self.config.port_start}-{self.config.port_end})"
        )

    def _create_options(self, browser_id: str, port: int) -> "ChromiumOptions":
        """创建浏览器配置"""
        user_data_path = str(self._data_path / browser_id)

        co = ChromiumOptions(read_file=False)

        # ==================== 基础设置 ====================
        co.set_local_port(port)

        if self.config.new_env:
            co.new_env(True)
        else:
            co.set_user_data_path(user_data_path)

        if self.config.browser_path:
            co.set_browser_path(self.config.browser_path)

        # ==================== 显示模式 ====================
        co.headless(self.config.headless)

        if self.config.incognito:
            co.incognito(True)

        if self.config.window_size:
            w, h = self.config.window_size.split(",")
            co.set_argument("--window-size", f"{w},{h}")

        # ==================== 网络设置 ====================
        if self.config.proxy:
            co.set_proxy(self.config.proxy)

        if self.config.ignore_certificate_errors:
            co.ignore_certificate_errors(True)

        # ==================== 内容加载 ====================
        if self.config.no_imgs:
            co.no_imgs(True)

        if self.config.no_js:
            co.no_js(True)

        if self.config.mute:
            co.mute(True)

        if self.config.load_mode in ("normal", "eager", "none"):
            co.set_load_mode(self.config.load_mode)

        # ==================== 超时和重试 ====================
        co.set_timeouts(
            base=self.config.base_timeout,
            page_load=self.config.page_load_timeout,
            script=self.config.script_timeout,
        )
        co.set_retry(times=self.config.retry_times, interval=self.config.retry_interval)

        # ==================== User-Agent ====================
        if self.config.user_agent:
            co.set_user_agent(self.config.user_agent)

        # ==================== 下载目录 ====================
        if self.config.download_path:
            co.set_download_path(self.config.download_path)

        # ==================== 扩展插件 ====================
        for ext_path in self.config.extensions:
            if os.path.exists(ext_path):
                co.add_extension(ext_path)

        # ==================== 自定义偏好设置 ====================
        for pref_name, pref_value in self.config.preferences.items():
            co.set_pref(pref_name, pref_value)

        co.set_pref("credentials_enable_service", False)

        # ==================== 实验性 flags ====================
        for flag_name, flag_value in self.config.flags.items():
            if flag_value:
                co.set_flag(flag_name, flag_value)
            else:
                co.set_flag(flag_name)

        # ==================== 常用优化参数 ====================
        default_arguments = [
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-notifications",
            "--disable-popup-blocking",
            "--hide-crash-restore-bubble",
        ]

        for arg in default_arguments:
            co.set_argument(arg)

        # ==================== 自定义命令行参数 ====================
        for arg in self.config.extra_arguments:
            if "=" in arg:
                key, value = arg.split("=", 1)
                co.set_argument(key, value)
            else:
                co.set_argument(arg)

        return co

    async def start(self) -> None:
        """启动浏览器池"""
        if not HAS_DRISSIONPAGE:
            raise RuntimeError("DrissionPage 未安装，请执行: pip install DrissionPage")

        self._running = True
        logger.info(f"浏览器池已启动 (最大实例: {self.config.max_browsers})")

    async def stop(self) -> None:
        """停止浏览器池，关闭所有实例"""
        self._running = False

        async with self._lock:
            for browser_id in list(self._instances.keys()):
                await self._close_browser(browser_id)

        logger.info("浏览器池已停止")

    async def _close_browser(self, browser_id: str) -> None:
        """关闭浏览器实例"""
        instance = self._instances.pop(browser_id, None)
        if not instance:
            return

        try:
            if instance.browser:
                instance.browser.quit()
            self._port_map.pop(instance.port, None)
            self._stats["browsers_closed"] += 1
            logger.debug(f"浏览器已关闭: {browser_id}")
        except Exception as e:
            logger.error(f"关闭浏览器失败 [{browser_id}]: {e}")

    async def acquire(self, browser_id: str | None = None) -> BrowserInstance:
        """
        获取浏览器实例

        Args:
            browser_id: 指定浏览器 ID（用于复用特定实例）

        Returns:
            BrowserInstance
        """
        async with self._lock:
            # 指定 ID 时尝试复用
            if browser_id and browser_id in self._instances:
                instance = self._instances[browser_id]
                instance.last_used = time.time()
                return instance

            # 查找空闲实例
            for inst in self._instances.values():
                if not inst.is_busy and inst.tab_count < self.config.max_tabs_per_browser:
                    inst.last_used = time.time()
                    return inst

            # 创建新实例
            if len(self._instances) < self.config.max_browsers:
                return await self._create_browser()

            # 等待空闲实例
            raise RuntimeError("浏览器池已满，请稍后重试")

    async def _create_browser(self) -> BrowserInstance:
        """创建新浏览器实例"""
        browser_id = f"browser_{uuid.uuid4().hex[:8]}"
        port = self._find_available_port()
        user_data_path = str(self._data_path / browser_id)

        options = self._create_options(browser_id, port)

        loop = asyncio.get_event_loop()
        browser = await loop.run_in_executor(None, lambda: Chromium(options))

        instance = BrowserInstance(
            browser_id=browser_id,
            port=port,
            user_data_path=user_data_path,
            browser=browser,
        )

        self._instances[browser_id] = instance
        self._port_map[port] = browser_id
        self._stats["browsers_created"] += 1

        logger.info(f"浏览器已创建: {browser_id} (端口: {port})")
        return instance

    async def release(self, instance: BrowserInstance) -> None:
        """释放浏览器实例"""
        instance.is_busy = False
        instance.last_used = time.time()

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        return {
            **self._stats,
            "active_browsers": len(self._instances),
            "max_browsers": self.config.max_browsers,
            "data_dir": str(self._data_path),
        }


@dataclass
class RenderResponse:
    """渲染响应"""

    url: str
    status: int = 200
    html: str = ""
    title: str = ""
    cookies: dict[str, str] = field(default_factory=dict)

    # 截图
    screenshot: bytes | None = None

    # 元数据
    elapsed_ms: float = 0
    browser_id: str = ""

    # 错误
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


class RenderClient:
    """
    DrissionPage 渲染客户端

    用法:
        client = RenderClient(RenderConfig(headless=True))
        await client.start()

        response = await client.render("https://example.com")
        logger.info(response.html)

        await client.close()
    """

    def __init__(self, config: RenderConfig | None = None):
        self.config = config or RenderConfig()
        self._pool = BrowserPool(self.config)
        self._running = False

    async def start(self) -> None:
        """启动客户端"""
        await self._pool.start()
        self._running = True
        logger.info("渲染客户端已启动")

    async def close(self) -> None:
        """关闭客户端"""
        self._running = False
        await self._pool.stop()
        logger.info("渲染客户端已关闭")

    async def render(
        self,
        url: str,
        *,
        wait: str | None = None,
        wait_timeout: float = 10,
        screenshot: bool = False,
        cookies: list[dict] | None = None,
        headers: dict[str, str] | None = None,
        browser_id: str | None = None,
    ) -> RenderResponse:
        """
        渲染页面

        Args:
            url: 目标 URL
            wait: 等待元素选择器
            wait_timeout: 等待超时（秒）
            screenshot: 是否截图
            cookies: 设置 cookies
            headers: 自定义 headers（部分支持）
            browser_id: 指定浏览器实例

        Returns:
            RenderResponse
        """
        start_time = time.time()
        response = RenderResponse(url=url)

        instance = None
        try:
            instance = await self._pool.acquire(browser_id)
            instance.is_busy = True
            response.browser_id = instance.browser_id

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._render_sync,
                instance,
                url,
                wait,
                wait_timeout,
                screenshot,
                cookies,
            )

            response.html = result.get("html", "")
            response.title = result.get("title", "")
            response.cookies = result.get("cookies", {})
            response.screenshot = result.get("screenshot")
            response.status = result.get("status", 200)
            response.error = result.get("error")

            self._pool._stats["pages_loaded"] += 1

        except Exception as e:
            logger.error(f"渲染失败 [{url}]: {e}")
            response.error = str(e)
            response.status = 0
            self._pool._stats["errors"] += 1

        finally:
            if instance:
                await self._pool.release(instance)

        response.elapsed_ms = (time.time() - start_time) * 1000
        return response

    def _render_sync(
        self,
        instance: BrowserInstance,
        url: str,
        wait: str | None,
        wait_timeout: float,
        screenshot: bool,
        cookies: list[dict] | None,
    ) -> dict[str, Any]:
        """同步渲染（在线程池中执行）"""
        result = {}

        try:
            browser = instance.browser
            tab = browser.latest_tab

            if cookies:
                for cookie in cookies:
                    tab.set.cookies(cookie)

            tab.get(url)

            if wait:
                with contextlib.suppress(Exception):
                    tab.ele(wait, timeout=wait_timeout)

            result["html"] = tab.html
            result["title"] = tab.title
            result["status"] = 200

            try:
                result["cookies"] = {c["name"]: c["value"] for c in tab.cookies()}
            except Exception:
                result["cookies"] = {}

            if screenshot:
                try:
                    result["screenshot"] = tab.get_screenshot(as_bytes="png")
                except Exception as e:
                    logger.warning(f"截图失败: {e}")

        except Exception as e:
            result["error"] = str(e)
            result["status"] = 0

        return result

    async def execute_script(
        self,
        url: str,
        script: str,
        *,
        wait: str | None = None,
        browser_id: str | None = None,
    ) -> dict[str, Any]:
        """
        执行 JavaScript 脚本

        Args:
            url: 目标 URL
            script: JavaScript 代码
            wait: 等待元素
            browser_id: 指定浏览器

        Returns:
            脚本执行结果
        """
        instance = None
        try:
            instance = await self._pool.acquire(browser_id)
            instance.is_busy = True

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._execute_script_sync,
                instance,
                url,
                script,
                wait,
            )
            return result

        finally:
            if instance:
                await self._pool.release(instance)

    def _execute_script_sync(
        self,
        instance: BrowserInstance,
        url: str,
        script: str,
        wait: str | None,
    ) -> dict[str, Any]:
        """同步执行脚本"""
        try:
            tab = instance.browser.latest_tab
            tab.get(url)

            if wait:
                tab.ele(wait, timeout=10)

            result = tab.run_js(script)
            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def interact(
        self,
        url: str,
        actions: list[dict[str, Any]],
        *,
        browser_id: str | None = None,
    ) -> RenderResponse:
        """
        交互式操作

        Args:
            url: 目标 URL
            actions: 操作列表，支持:
                - {"action": "click", "selector": "..."}
                - {"action": "input", "selector": "...", "text": "..."}
                - {"action": "wait", "selector": "...", "timeout": 10}
                - {"action": "screenshot"}
                - {"action": "scroll", "x": 0, "y": 500}
            browser_id: 指定浏览器

        Returns:
            RenderResponse
        """
        start_time = time.time()
        response = RenderResponse(url=url)

        instance = None
        try:
            instance = await self._pool.acquire(browser_id)
            instance.is_busy = True
            response.browser_id = instance.browser_id

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._interact_sync,
                instance,
                url,
                actions,
            )

            response.html = result.get("html", "")
            response.title = result.get("title", "")
            response.screenshot = result.get("screenshot")
            response.error = result.get("error")
            response.status = 200 if not result.get("error") else 0

        except Exception as e:
            response.error = str(e)
            response.status = 0

        finally:
            if instance:
                await self._pool.release(instance)

        response.elapsed_ms = (time.time() - start_time) * 1000
        return response

    def _interact_sync(
        self,
        instance: BrowserInstance,
        url: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """同步交互操作"""
        result = {}

        try:
            tab = instance.browser.latest_tab
            tab.get(url)

            for action in actions:
                action_type = action.get("action")
                selector = action.get("selector")

                if action_type == "click":
                    ele = tab.ele(selector, timeout=action.get("timeout", 10))
                    ele.click()

                elif action_type == "input":
                    ele = tab.ele(selector, timeout=action.get("timeout", 10))
                    if action.get("clear", True):
                        ele.clear()
                    ele.input(action.get("text", ""))

                elif action_type == "wait":
                    tab.ele(selector, timeout=action.get("timeout", 10))

                elif action_type == "screenshot":
                    result["screenshot"] = tab.get_screenshot(as_bytes="png")

                elif action_type == "scroll":
                    tab.scroll.to_location(action.get("x", 0), action.get("y", 0))

                elif action_type == "sleep":
                    time.sleep(action.get("seconds", 1))

            result["html"] = tab.html
            result["title"] = tab.title

        except Exception as e:
            result["error"] = str(e)

        return result

    def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        return self._pool.get_stats()
