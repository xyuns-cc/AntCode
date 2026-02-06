"""测试执行服务

实现测试批次的创建、执行、结果收集和清理功能。
测试批次用于在正式执行前验证爬取规则是否正确。

需求: 12.1, 12.2, 12.3, 12.4, 12.5
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from loguru import logger

from antcode_core.common.exceptions import BatchNotFoundError, BatchStateError
from antcode_core.domain.models.crawl import BatchStatus, CrawlBatch
from antcode_core.domain.models.project import Project
from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.batch_service import CrawlBatchService, crawl_batch_service
from antcode_core.application.services.crawl.dedup_service import CrawlDedupService, crawl_dedup_service
from antcode_core.application.services.crawl.progress_service import (
    CrawlProgressService,
    crawl_progress_service,
)
from antcode_core.application.services.crawl.queue_service import CrawlQueueService, crawl_queue_service

# 测试批次默认配置
DEFAULT_TEST_MAX_DEPTH = 2
DEFAULT_TEST_MAX_PAGES = 10
DEFAULT_TEST_TIMEOUT = 60  # 测试超时时间（秒）
DEFAULT_TEST_CONCURRENCY = 5


@dataclass
class CrawlTestResult:
    """测试执行结果数据类"""

    batch_id: str = ""
    success: bool = False
    total_pages: int = 0
    success_pages: int = 0
    failed_pages: int = 0
    sample_data: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "batch_id": self.batch_id,
            "success": self.success,
            "total_pages": self.total_pages,
            "success_pages": self.success_pages,
            "failed_pages": self.failed_pages,
            "sample_data": self.sample_data,
            "errors": self.errors,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrawlTestResult:
        """从字典创建结果对象"""
        return cls(
            batch_id=data.get("batch_id", ""),
            success=data.get("success", False),
            total_pages=int(data.get("total_pages", 0)),
            success_pages=int(data.get("success_pages", 0)),
            failed_pages=int(data.get("failed_pages", 0)),
            sample_data=data.get("sample_data", []),
            errors=data.get("errors", []),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
        )


@dataclass
class CrawlTestConfig:
    """测试配置数据类"""

    max_depth: int = DEFAULT_TEST_MAX_DEPTH
    max_pages: int = DEFAULT_TEST_MAX_PAGES
    timeout: int = DEFAULT_TEST_TIMEOUT
    concurrency: int = DEFAULT_TEST_CONCURRENCY

    def validate(self) -> tuple:
        """验证配置是否有效

        Returns:
            (is_valid, error_message) 元组

        需求: 12.1, 12.2 - 测试批次应限制最大爬取页面数和深度
        """
        errors = []

        # 验证深度限制
        if self.max_depth < 1:
            errors.append("最大深度必须大于等于 1")
        if self.max_depth > 3:
            errors.append("测试批次最大深度不能超过 3")

        # 验证页面数限制
        if self.max_pages < 1:
            errors.append("最大页面数必须大于等于 1")
        if self.max_pages > 100:
            errors.append("测试批次最大页面数不能超过 100")

        # 验证超时
        if self.timeout < 10:
            errors.append("超时时间必须大于等于 10 秒")
        if self.timeout > 300:
            errors.append("超时时间不能超过 300 秒")

        # 验证并发数
        if self.concurrency < 1:
            errors.append("并发数必须大于等于 1")
        if self.concurrency > 10:
            errors.append("测试批次并发数不能超过 10")

        is_valid = len(errors) == 0
        error_message = "; ".join(errors) if errors else ""

        return is_valid, error_message

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "timeout": self.timeout,
            "concurrency": self.concurrency,
        }


class CrawlTestService(BaseService):
    """测试执行服务

    实现测试批次的完整生命周期管理，包括：
    - 创建测试批次（带限制）
    - 执行测试
    - 收集结果
    - 清理测试数据

    需求: 12.1, 12.2, 12.3, 12.4, 12.5
    """

    def __init__(
        self,
        batch_service: CrawlBatchService = None,
        queue_service: CrawlQueueService = None,
        progress_service: CrawlProgressService = None,
        dedup_service: CrawlDedupService = None,
    ):
        """初始化测试执行服务

        Args:
            batch_service: 批次管理服务，为 None 时使用全局实例
            queue_service: 队列服务，为 None 时使用全局实例
            progress_service: 进度服务，为 None 时使用全局实例
            dedup_service: 去重服务，为 None 时使用全局实例
        """
        super().__init__()
        self._batch_service = batch_service or crawl_batch_service
        self._queue_service = queue_service or crawl_queue_service
        self._progress_service = progress_service or crawl_progress_service
        self._dedup_service = dedup_service or crawl_dedup_service

        # 存储测试结果（内存缓存）
        self._test_results = {}  # {batch_id: CrawlTestResult}

    # =========================================================================
    # 测试批次创建
    # =========================================================================

    async def create_test_batch(
        self,
        project_id: str,
        seed_urls: list,
        user_id: int,
        max_depth: int = DEFAULT_TEST_MAX_DEPTH,
        max_pages: int = DEFAULT_TEST_MAX_PAGES,
        timeout: int = DEFAULT_TEST_TIMEOUT,
        concurrency: int = DEFAULT_TEST_CONCURRENCY,
    ) -> CrawlBatch:
        """创建测试批次

        创建一个限制范围的测试批次，用于验证爬取规则。

        Args:
            project_id: 项目公开 ID
            seed_urls: 种子 URL 列表
            user_id: 创建者 ID
            max_depth: 最大爬取深度（默认 2，最大 3）
            max_pages: 最大爬取页面数（默认 10，最大 100）
            timeout: 超时时间（秒）
            concurrency: 并发数

        Returns:
            创建的 CrawlBatch 对象

        Raises:
            ValueError: 配置无效时抛出

        需求: 12.1 - 用户请求测试执行时创建一个限制范围的测试批次
        需求: 12.2 - 测试批次执行时限制最大爬取页面数和深度
        """
        # 验证配置
        config = CrawlTestConfig(
            max_depth=max_depth,
            max_pages=max_pages,
            timeout=timeout,
            concurrency=concurrency,
        )

        is_valid, error_message = config.validate()
        if not is_valid:
            raise ValueError(f"测试配置无效: {error_message}")

        # 限制种子 URL 数量
        if len(seed_urls) > 10:
            seed_urls = seed_urls[:10]
            logger.warning("测试批次种子 URL 数量超过限制，已截断为 10 个")

        # 生成测试批次名称
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"测试批次_{timestamp}"

        # 创建测试批次
        batch = await self._batch_service.create_batch(
            project_id=project_id,
            name=name,
            seed_urls=seed_urls,
            user_id=user_id,
            description="自动创建的测试批次",
            max_depth=max_depth,
            max_pages=max_pages,
            max_concurrency=concurrency,
            request_delay=0.5,
            timeout=30,
            max_retries=1,  # 测试批次减少重试次数
            is_test=True,
        )

        logger.info(f"创建测试批次: batch_id={batch.public_id}, project={project_id}, "
                    f"seed_urls={len(seed_urls)}, max_depth={max_depth}, "
                    f"max_pages={max_pages}")

        return batch

    # =========================================================================
    # 测试执行
    # =========================================================================

    async def start_test(
        self,
        batch_id: str,
    ) -> CrawlBatch:
        """启动测试执行

        Args:
            batch_id: 测试批次公开 ID

        Returns:
            更新后的 CrawlBatch 对象

        Raises:
            BatchNotFoundError: 批次不存在时抛出
            BatchStateError: 批次状态不允许启动时抛出
        """
        batch = await self._batch_service.get_batch(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        if not batch.is_test:
            raise BatchStateError(
                batch_id=batch_id,
                current_state=batch.status,
                expected_states=["测试批次"],
            )

        # 启动批次
        batch = await self._batch_service.start_batch(batch_id)

        # 初始化测试结果
        self._test_results[batch_id] = CrawlTestResult(
            batch_id=batch_id,
            success=False,
            total_pages=0,
            success_pages=0,
            failed_pages=0,
            sample_data=[],
            errors=[],
            duration_seconds=0.0,
        )

        logger.info(f"启动测试执行: batch_id={batch_id}")

        return batch

    async def execute_test(
        self,
        project_id: str,
        seed_urls: list,
        user_id: int,
        max_depth: int = DEFAULT_TEST_MAX_DEPTH,
        max_pages: int = DEFAULT_TEST_MAX_PAGES,
        timeout: int = DEFAULT_TEST_TIMEOUT,
    ) -> CrawlTestResult:
        """执行完整的测试流程

        创建测试批次、启动执行、等待完成、收集结果、清理数据。

        Args:
            project_id: 项目公开 ID
            seed_urls: 种子 URL 列表
            user_id: 创建者 ID
            max_depth: 最大爬取深度
            max_pages: 最大爬取页面数
            timeout: 超时时间（秒）

        Returns:
            CrawlTestResult 对象

        需求: 12.1, 12.2, 12.3, 12.4, 12.5
        """
        start_time = time.time()
        result = CrawlTestResult()

        try:
            # 创建测试批次
            batch = await self.create_test_batch(
                project_id=project_id,
                seed_urls=seed_urls,
                user_id=user_id,
                max_depth=max_depth,
                max_pages=max_pages,
                timeout=timeout,
            )
            result.batch_id = batch.public_id

            # 启动测试
            await self.start_test(batch.public_id)

            # 等待测试完成或超时
            result = await self._wait_for_completion(
                batch_id=batch.public_id,
                project_id=project_id,
                timeout=timeout,
            )

        except Exception as e:
            # 记录错误
            result.success = False
            result.errors.append(str(e))
            logger.error(f"测试执行失败: project={project_id}, 错误: {e}")

        finally:
            # 计算执行时间
            result.duration_seconds = round(time.time() - start_time, 2)

            # 清理测试数据
            if result.batch_id:
                try:
                    await self.cleanup_test(result.batch_id)
                except Exception as e:
                    logger.error(f"清理测试数据失败: batch_id={result.batch_id}, 错误: {e}")

        return result

    async def _wait_for_completion(
        self,
        batch_id: str,
        project_id: str,
        timeout: int,
    ) -> CrawlTestResult:
        """等待测试完成

        Args:
            batch_id: 批次 ID
            project_public_id: 项目公开 ID（可选，避免重复查询）
            project_id: 项目 ID
            timeout: 超时时间（秒）

        Returns:
            CrawlTestResult 对象
        """
        result = CrawlTestResult(batch_id=batch_id)
        start_time = time.time()

        while True:
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                result.success = False
                result.errors.append(f"测试执行超时（{timeout}秒）")
                logger.warning(f"测试执行超时: batch_id={batch_id}, timeout={timeout}")
                break

            # 获取批次状态
            batch = await self._batch_service.get_batch(batch_id)
            if not batch:
                result.success = False
                result.errors.append("批次不存在")
                break

            # 检查是否完成
            if batch.status in [BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED]:
                # 收集结果
                result = await self._collect_results(batch_id, project_id)
                result.success = batch.status == BatchStatus.COMPLETED

                if batch.status == BatchStatus.FAILED:
                    result.errors.append("测试执行失败")
                elif batch.status == BatchStatus.CANCELLED:
                    result.errors.append("测试被取消")

                break

            # 检查是否达到页面限制
            progress = await self._progress_service.get_progress(project_id, batch_id)
            if progress:
                total_processed = progress.completed_urls + progress.failed_urls
                if total_processed >= batch.max_pages:
                    # 达到限制，标记完成
                    await self._batch_service.complete_batch(batch_id, success=True)
                    result = await self._collect_results(batch_id, project_id)
                    result.success = True
                    break

            # 等待一段时间后再检查
            await asyncio.sleep(1)

        return result

    # =========================================================================
    # 结果收集
    # =========================================================================

    async def _collect_results(
        self,
        batch_id: str,
        project_id: str,
    ) -> CrawlTestResult:
        """收集测试结果

        Args:
            batch_id: 批次 ID
            project_id: 项目 ID

        Returns:
            CrawlTestResult 对象

        需求: 12.3 - 测试执行完成时返回详细的执行结果和样本数据
        """
        result = CrawlTestResult(batch_id=batch_id)

        # 获取进度信息
        progress = await self._progress_service.get_progress(project_id, batch_id)
        if progress:
            result.total_pages = progress.completed_urls + progress.failed_urls
            result.success_pages = progress.completed_urls
            result.failed_pages = progress.failed_urls

        # 获取缓存的测试结果（包含样本数据）
        cached_result = self._test_results.get(batch_id)
        if cached_result:
            result.sample_data = cached_result.sample_data
            result.errors = cached_result.errors

        logger.info(f"收集测试结果: batch_id={batch_id}, "
                    f"total={result.total_pages}, success={result.success_pages}, "
                    f"failed={result.failed_pages}")

        return result

    async def add_sample_data(
        self,
        batch_id: str,
        data: dict,
    ) -> bool:
        """添加样本数据

        Args:
            batch_id: 批次 ID
            data: 样本数据

        Returns:
            是否添加成功
        """
        if batch_id not in self._test_results:
            self._test_results[batch_id] = CrawlTestResult(batch_id=batch_id)

        result = self._test_results[batch_id]

        # 限制样本数据数量
        if len(result.sample_data) < 10:
            result.sample_data.append(data)
            return True

        return False

    async def add_error(
        self,
        batch_id: str,
        error: str,
    ) -> bool:
        """添加错误信息

        Args:
            batch_id: 批次 ID
            error: 错误信息

        Returns:
            是否添加成功

        需求: 12.4 - 测试执行失败时返回具体的错误信息和失败原因
        """
        if batch_id not in self._test_results:
            self._test_results[batch_id] = CrawlTestResult(batch_id=batch_id)

        result = self._test_results[batch_id]

        # 限制错误信息数量
        if len(result.errors) < 50:
            result.errors.append(error)
            return True

        return False

    async def get_test_result(
        self,
        batch_id: str,
    ) -> CrawlTestResult | None:
        """获取测试结果

        Args:
            batch_id: 批次 ID

        Returns:
            CrawlTestResult 对象，不存在时返回 None
        """
        return self._test_results.get(batch_id)

    # =========================================================================
    # 测试清理
    # =========================================================================

    async def cleanup_test(
        self,
        batch_id: str,
        project_public_id: str | None = None,
    ) -> bool:
        """清理测试数据

        清理测试批次相关的所有数据，包括队列、进度、去重数据等。

        Args:
            batch_id: 批次 ID

        Returns:
            是否清理成功

        需求: 12.5 - 测试批次结束时自动清理测试数据不影响正式数据
        """
        try:
            batch = await self._batch_service.get_batch(batch_id)
            if not batch:
                logger.warning(f"清理测试数据时批次不存在: batch_id={batch_id}")
                return False

            if not batch.is_test:
                logger.warning(f"尝试清理非测试批次: batch_id={batch_id}")
                return False

            if project_public_id is None:
                project = await Project.filter(id=batch.project_id).only("public_id").first()
                project_public_id = project.public_id if project else str(batch.project_id)

            # 清理队列数据
            await self._queue_service.clear_queues(project_public_id)

            # 清理进度数据
            await self._progress_service.clear_progress(project_public_id, batch_id)

            # 清理去重数据（测试批次使用独立的去重过滤器）
            # 注意：这里不清理去重数据，因为测试和正式共用去重过滤器
            # 如果需要独立的测试去重，可以使用不同的 project_id 前缀

            # 清理内存中的测试结果
            self._test_results.pop(batch_id, None)

            # 更新批次状态为已取消（如果还在运行）
            if batch.status in [BatchStatus.PENDING, BatchStatus.RUNNING, BatchStatus.PAUSED]:
                await self._batch_service.cancel_batch(batch_id, cleanup=False)

            logger.info(f"清理测试数据完成: batch_id={batch_id}")

            return True

        except Exception as e:
            logger.error(f"清理测试数据失败: batch_id={batch_id}, 错误: {e}")
            return False

    async def cleanup_expired_tests(
        self,
        project_id: str = None,
        max_age_hours: int = 24,
    ) -> int:
        """清理过期的测试批次

        Args:
            project_id: 项目公开 ID（可选，不指定则清理所有项目的）
            max_age_hours: 最大保留时间（小时）

        Returns:
            清理的批次数量
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        query = CrawlBatch.filter(
            is_test=True,
            created_at__lt=cutoff,
        )

        if project_id:
            project = await Project.filter(public_id=project_id).first()
            if project:
                query = query.filter(project_id=project.id)
            else:
                return 0

        batches = await query.all()
        cleaned_count = 0

        project_public_id_map = {}
        project_ids = list({b.project_id for b in batches})
        if project_ids:
            project_public_id_map = await self._batch_service.query.batch_get_project_public_ids(
                project_ids
            )

        for batch in batches:
            try:
                project_public_id = project_public_id_map.get(
                    batch.project_id, str(batch.project_id)
                )
                success = await self.cleanup_test(
                    batch.public_id,
                    project_public_id=project_public_id,
                )
                if success:
                    # 删除批次记录
                    await batch.delete()
                    cleaned_count += 1
            except Exception as e:
                logger.error(f"清理过期测试批次失败: batch_id={batch.public_id}, 错误: {e}")

        logger.info(f"清理过期测试批次: project={project_id}, cleaned={cleaned_count}")

        return cleaned_count

    # =========================================================================
    # 测试状态查询
    # =========================================================================

    async def get_test_status(
        self,
        batch_id: str,
    ) -> dict:
        """获取测试状态

        Args:
            batch_id: 批次 ID

        Returns:
            测试状态字典
        """
        batch = await self._batch_service.get_batch(batch_id)
        if not batch:
            return {
                "batch_id": batch_id,
                "status": "not_found",
                "is_test": False,
            }

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 获取进度
        progress = await self._progress_service.get_progress(project_public_id, batch_id)

        # 获取测试结果
        result = self._test_results.get(batch_id)

        status = {
            "batch_id": batch_id,
            "project_id": project_public_id,
            "status": batch.status,
            "is_test": batch.is_test,
            "config": {
                "max_depth": batch.max_depth,
                "max_pages": batch.max_pages,
            },
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "started_at": batch.started_at.isoformat() if batch.started_at else None,
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        }

        if progress:
            status["progress"] = {
                "total_urls": progress.total_urls,
                "completed_urls": progress.completed_urls,
                "failed_urls": progress.failed_urls,
                "pending_urls": progress.pending_urls,
            }

        if result:
            status["result"] = {
                "success": result.success,
                "sample_count": len(result.sample_data),
                "error_count": len(result.errors),
            }

        return status

    async def list_test_batches(
        self,
        project_id: str = None,
        user_id: int = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple:
        """列出测试批次

        Args:
            project_id: 项目公开 ID（可选）
            user_id: 用户 ID（可选）
            page: 页码
            size: 每页数量

        Returns:
            (batches, total) 元组
        """
        return await self._batch_service.list_batches(
            project_id=project_id,
            user_id=user_id,
            is_test=True,
            page=page,
            size=size,
        )

    # =========================================================================
    # 限制检查
    # =========================================================================

    def check_depth_limit(self, current_depth: int, max_depth: int) -> bool:
        """检查深度是否超过限制

        Args:
            current_depth: 当前深度
            max_depth: 最大深度

        Returns:
            True 表示未超限，False 表示已超限

        需求: 12.2 - 测试批次执行时限制爬取深度
        """
        return current_depth < max_depth

    def check_page_limit(self, current_pages: int, max_pages: int) -> bool:
        """检查页面数是否超过限制

        Args:
            current_pages: 当前页面数
            max_pages: 最大页面数

        Returns:
            True 表示未超限，False 表示已超限

        需求: 12.2 - 测试批次执行时限制最大爬取页面数
        """
        return current_pages < max_pages

    async def should_continue_test(
        self,
        batch_id: str,
        project_id: str,
    ) -> tuple:
        """检查测试是否应该继续

        Args:
            batch_id: 批次 ID
            project_id: 项目 ID

        Returns:
            (should_continue, reason) 元组
        """
        batch = await self._batch_service.get_batch(batch_id)
        if not batch:
            return False, "批次不存在"

        if batch.status != BatchStatus.RUNNING:
            return False, f"批次状态为 {batch.status}"

        # 获取进度
        progress = await self._progress_service.get_progress(project_id, batch_id)
        if not progress:
            return True, ""

        # 检查页面限制
        total_processed = progress.completed_urls + progress.failed_urls
        if not self.check_page_limit(total_processed, batch.max_pages):
            return False, f"已达到最大页面数限制 ({batch.max_pages})"

        # 检查是否还有待处理的 URL
        if progress.pending_urls <= 0 and total_processed > 0:
            return False, "所有 URL 已处理完成"

        return True, ""


# 全局服务实例
crawl_test_service = CrawlTestService()
