"""
渲染爬虫示例 - DrissionPage 浏览器抓取

演示:
1. 基础页面渲染
2. 等待动态内容加载
3. 交互式操作（点击、输入）
4. 截图
5. 会话保持（复用浏览器实例）
"""

from collections.abc import AsyncGenerator

from ..render_client import RenderResponse
from ..render_spider import RenderSpider


class BasicRenderSpider(RenderSpider):
    """
    基础渲染爬虫示例

    抓取需要 JavaScript 渲染的页面
    """

    name = "basic_render_spider"
    start_urls = ["https://quotes.toscrape.com/js/"]

    # 等待页面内容加载完成
    wait_selector = ".quote"

    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """解析页面"""
        if not response.ok:
            return

        # 使用 CSS 选择器提取数据
        quotes = self.css(response.html, ".quote")

        for quote_html in quotes:
            yield {
                "text": self.css_first(quote_html, ".text::text"),
                "author": self.css_first(quote_html, ".author::text"),
                "tags": self.css(quote_html, ".tag::text"),
            }

        # 检查是否有下一页
        next_url = self.css_first(response.html, ".next a::attr(href)")
        if next_url:
            yield f"https://quotes.toscrape.com{next_url}"


class InteractiveSpider(RenderSpider):
    """
    交互式爬虫示例

    演示登录、表单提交等交互操作
    """

    name = "interactive_spider"
    start_urls = ["https://quotes.toscrape.com/login"]

    # 使用固定浏览器实例保持会话
    browser_id = "session_browser"

    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """登录并抓取"""
        # 执行登录操作
        login_response = await self.interact(
            response.url,
            [
                {"action": "input", "selector": "#username", "text": "test_user"},
                {"action": "input", "selector": "#password", "text": "test_pass"},
                {"action": "click", "selector": "input[type='submit']"},
                {"action": "wait", "selector": ".header-box", "timeout": 5},
            ],
        )

        if login_response.ok:
            yield {"status": "logged_in", "title": login_response.title}

            # 登录后访问其他页面
            quotes_response = await self.render("https://quotes.toscrape.com/")
            if quotes_response.ok:
                for quote in self.css(quotes_response.html, ".quote .text::text"):
                    yield {"quote": quote}


class ScreenshotSpider(RenderSpider):
    """
    截图爬虫示例

    抓取页面并保存截图
    """

    name = "screenshot_spider"
    start_urls = ["https://example.com"]
    screenshot_on_error = True

    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """抓取并截图"""
        # 渲染页面并截图
        screenshot_response = await self.render(response.url, screenshot=True)

        yield {
            "url": screenshot_response.url,
            "title": screenshot_response.title,
            "has_screenshot": screenshot_response.screenshot is not None,
            "screenshot_size": (
                len(screenshot_response.screenshot)
                if screenshot_response.screenshot
                else 0
            ),
        }


class InfiniteScrollSpider(RenderSpider):
    """
    无限滚动爬虫示例

    处理懒加载/无限滚动页面
    """

    name = "infinite_scroll_spider"
    start_urls = ["https://quotes.toscrape.com/scroll"]

    # 滚动配置
    max_scrolls = 5
    scroll_delay = 1.0

    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """滚动加载并抓取"""
        all_quotes = set()
        scroll_response = response

        for i in range(self.max_scrolls):
            # 执行滚动
            scroll_response = await self.interact(
                response.url if i == 0 else scroll_response.url,
                [
                    {"action": "scroll", "x": 0, "y": 10000},
                    {"action": "sleep", "seconds": self.scroll_delay},
                ],
            )

            if not scroll_response.ok:
                break

            # 提取新加载的内容
            quotes = self.css(scroll_response.html, ".quote .text::text")
            new_quotes = set(quotes) - all_quotes

            for quote in new_quotes:
                yield {"quote": quote, "scroll_page": i + 1}

            all_quotes.update(quotes)

            # 如果没有新内容，停止滚动
            if not new_quotes:
                break


class JavaScriptSpider(RenderSpider):
    """
    JavaScript 执行爬虫示例

    在页面中执行自定义 JavaScript
    """

    name = "javascript_spider"
    start_urls = ["https://example.com"]

    async def parse(
        self, response: RenderResponse
    ) -> AsyncGenerator[dict | str, None]:
        """执行 JS 并提取数据"""
        # 执行 JavaScript 获取页面信息
        js_result = await self.execute_js(
            response.url,
            """
            return {
                title: document.title,
                url: window.location.href,
                links: Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.textContent.trim(),
                    href: a.href
                })),
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight
                }
            };
            """,
        )

        if js_result.get("success"):
            yield js_result.get("result", {})


# 使用示例
if __name__ == "__main__":
    import asyncio

    from loguru import logger
    from antcode_worker.config import DATA_ROOT

    from ..render_client import RenderClient, RenderConfig

    async def main():
        # 创建渲染客户端
        config = RenderConfig(
            max_browsers=2,
            headless=True,
            data_dir=str(DATA_ROOT / "browsers"),
        )
        client = RenderClient(config)
        await client.start()

        try:
            # 运行爬虫
            spider = BasicRenderSpider()
            result = await spider.run(client=client)

            logger.info("抓取完成: {} 条数据", result.items_count)
            for item in result.items[:5]:
                logger.info("  - {}", item)

        finally:
            await client.close()

    asyncio.run(main())
