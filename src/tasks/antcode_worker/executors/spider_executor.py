"""爬虫执行器"""
import asyncio
from datetime import datetime
from loguru import logger

from .base import BaseExecutor, ExecutionResult, ExecutionStatus

try:
    from ..spider.base import Spider
    from ..spider.client import HttpClient, ClientConfig
    SPIDER_AVAILABLE = True
except ImportError:
    SPIDER_AVAILABLE = False
    Spider = None
    HttpClient = None
    ClientConfig = None


class SpiderExecutor(BaseExecutor):
    def __init__(self, signals=None, max_concurrent=5, default_timeout=3600, client_config=None):
        super().__init__(signals, max_concurrent, default_timeout)
        self.client_config = client_config or (ClientConfig() if ClientConfig else None)
        self._http_client = None

    async def start(self):
        await super().start()
        if HttpClient and self.client_config:
            self._http_client = HttpClient(self.client_config)
            await self._http_client.start()

    async def stop(self):
        if self._http_client:
            await self._http_client.close()
        await super().stop()

    async def _do_execute(self, context):
        result = ExecutionResult(execution_id=context.execution_id, status=ExecutionStatus.RUNNING,
                                started_at=datetime.now().isoformat())
        try:
            spider_class = await self._load_spider(context.project_path, context.entry_point)
            if not spider_class:
                result.status = ExecutionStatus.FAILED
                result.error_message = "无法加载爬虫类"
                result.finished_at = datetime.now().isoformat()
                return result

            spider = spider_class(**context.params)
            async with self._lock:
                self._running_tasks[context.execution_id] = spider

            try:
                crawl_result = await asyncio.wait_for(spider.run(client=self._http_client),
                                                     timeout=context.timeout or self.default_timeout)
                result.status = ExecutionStatus.COMPLETED
                result.data = crawl_result.to_dict()
                result.finished_at = datetime.now().isoformat()
            except asyncio.TimeoutError:
                result.status = ExecutionStatus.TIMEOUT
                result.error_message = f"爬虫超时 ({context.timeout}s)"
                result.finished_at = datetime.now().isoformat()
            finally:
                async with self._lock:
                    self._running_tasks.pop(context.execution_id, None)
        except Exception as e:
            logger.error(f"爬虫执行异常: {e}")
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)
            result.finished_at = datetime.now().isoformat()
        return result

    async def _load_spider(self, project_path, entry_point):
        """加载爬虫类，使用基类的 _load_module_class 方法"""
        if not SPIDER_AVAILABLE or Spider is None:
            logger.error("Spider 基类不可用")
            return None

        return await self._load_module_class(
            project_path=project_path,
            entry_point=entry_point,
            base_class=Spider,
            class_name_hint="Spider",
            module_name="spider_module",
        )

    async def cancel(self, execution_id):
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
