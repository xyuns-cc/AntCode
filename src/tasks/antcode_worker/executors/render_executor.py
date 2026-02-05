"""
渲染执行器 - DrissionPage 浏览器抓取

支持:
- 浏览器多开
- 用户数据持久化
- 交互式操作
- JavaScript 执行
- 截图
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger

from .base import BaseExecutor, ExecutionContext, ExecutionResult, ExecutionStatus

# 尝试导入渲染客户端
try:
    from ..spider.render_client import RenderClient, RenderConfig, RenderResponse
    RENDER_AVAILABLE = True
except ImportError:
    RENDER_AVAILABLE = False
    RenderClient = None
    RenderConfig = None
    RenderResponse = None

# 尝试导入 RenderSpider 基类
try:
    from ..spider.render_spider import RenderSpider
    RENDER_SPIDER_AVAILABLE = True
except ImportError:
    RENDER_SPIDER_AVAILABLE = False
    RenderSpider = None


class RenderExecutor(BaseExecutor):
    """
    渲染执行器
    
    用于执行需要浏览器渲染的爬虫任务
    """

    def __init__(
        self,
        signals=None,
        max_concurrent: int = 3,
        default_timeout: int = 3600,
        render_config: Optional["RenderConfig"] = None,
    ):
        super().__init__(signals, max_concurrent, default_timeout)

        # 渲染配置
        if render_config:
            self._render_config = render_config
        else:
            self._render_config = self._create_default_config()

        self._render_client: Optional["RenderClient"] = None

    def _create_default_config(self) -> "RenderConfig":
        """创建默认配置（使用合理默认值，无需环境变量）"""
        if not RenderConfig:
            return None

        return RenderConfig(
            # 浏览器池
            max_browsers=3,
            max_tabs_per_browser=5,
            data_dir="data/browsers",
            port_start=9200,
            port_end=9300,
            # 显示模式
            headless=True,
            window_size="1920,1080",
            # 超时
            page_load_timeout=30,
            script_timeout=30,
            base_timeout=10,
            # 重试
            retry_times=3,
            retry_interval=2.0,
            # 网络
            ignore_certificate_errors=True,
            # 内容加载
            mute=True,
            load_mode="normal",
        )

    async def start(self) -> None:
        """启动执行器"""
        await super().start()

        if not RENDER_AVAILABLE:
            logger.warning("DrissionPage 未安装，渲染执行器功能受限")
            return

        if self._render_config:
            self._render_client = RenderClient(self._render_config)
            await self._render_client.start()
            logger.info("渲染客户端已启动")

    async def stop(self) -> None:
        """停止执行器"""
        if self._render_client:
            await self._render_client.close()
            self._render_client = None

        await super().stop()

    async def _do_execute(self, context: ExecutionContext) -> ExecutionResult:
        """执行渲染任务"""
        result = ExecutionResult(
            execution_id=context.execution_id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )

        try:
            # 检查渲染客户端
            if not self._render_client:
                result.status = ExecutionStatus.FAILED
                result.error_message = "渲染客户端未初始化"
                result.finished_at = datetime.now().isoformat()
                return result

            # 加载渲染爬虫
            spider_class = await self._load_render_spider(
                context.project_path,
                context.entry_point
            )

            if not spider_class:
                result.status = ExecutionStatus.FAILED
                result.error_message = "无法加载渲染爬虫类"
                result.finished_at = datetime.now().isoformat()
                return result

            # 创建爬虫实例
            spider = spider_class(**context.params)

            # 注册到运行任务
            async with self._lock:
                self._running_tasks[context.execution_id] = spider

            try:
                # 执行爬虫
                crawl_result = await asyncio.wait_for(
                    spider.run(client=self._render_client),
                    timeout=context.timeout or self.default_timeout
                )

                result.status = ExecutionStatus.SUCCESS
                result.data = crawl_result if isinstance(crawl_result, dict) else {"result": crawl_result}

            except asyncio.TimeoutError:
                result.status = ExecutionStatus.TIMEOUT
                result.error_message = f"渲染任务超时 ({context.timeout}s)"

            finally:
                async with self._lock:
                    self._running_tasks.pop(context.execution_id, None)

        except Exception as e:
            logger.error(f"渲染执行异常: {e}")
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)

        result.finished_at = datetime.now().isoformat()
        return result

    async def _load_render_spider(
        self,
        project_path: str,
        entry_point: str
    ) -> Optional[type]:
        """加载渲染爬虫类，使用基类的 _load_module_class 方法"""
        # 如果 RenderSpider 基类可用，使用基类检查
        if RENDER_SPIDER_AVAILABLE and RenderSpider is not None:
            return await self._load_module_class(
                project_path=project_path,
                entry_point=entry_point,
                base_class=RenderSpider,
                class_name_hint="RenderSpider",
                module_name="render_spider_module",
            )

        # 否则使用方法检查
        return await self._load_module_class(
            project_path=project_path,
            entry_point=entry_point,
            class_name_hint="RenderSpider",
            module_name="render_spider_module",
            check_methods=["run", "parse"],
        )

    async def cancel(self, execution_id: str) -> bool:
        """取消执行，统一取消逻辑"""
        async with self._lock:
            spider = self._running_tasks.get(execution_id)
            if not spider:
                return False

        try:
            # 设置爬虫停止标志
            if hasattr(spider, '_running'):
                spider._running = False

            # 从运行任务列表中移除
            async with self._lock:
                self._running_tasks.pop(execution_id, None)

            logger.info(f"任务已取消: {execution_id}")
            return True
        except Exception as e:
            logger.error(f"取消任务失败: {e}")
            return False

    async def render_url(
        self,
        url: str,
        *,
        wait: Optional[str] = None,
        screenshot: bool = False,
        cookies: Optional[List[Dict]] = None,
    ) -> Optional["RenderResponse"]:
        """
        直接渲染 URL（便捷方法）
        
        Args:
            url: 目标 URL
            wait: 等待元素选择器
            screenshot: 是否截图
            cookies: cookies
            
        Returns:
            RenderResponse
        """
        if not self._render_client:
            logger.error("渲染客户端未初始化")
            return None

        return await self._render_client.render(
            url,
            wait=wait,
            screenshot=screenshot,
            cookies=cookies,
        )

    async def interact(
        self,
        url: str,
        actions: List[Dict[str, Any]],
    ) -> Optional["RenderResponse"]:
        """
        交互式操作（便捷方法）
        
        Args:
            url: 目标 URL
            actions: 操作列表
            
        Returns:
            RenderResponse
        """
        if not self._render_client:
            logger.error("渲染客户端未初始化")
            return None

        return await self._render_client.interact(url, actions)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        stats = super().get_stats()

        if self._render_client:
            stats["render"] = self._render_client.get_stats()

        return stats
