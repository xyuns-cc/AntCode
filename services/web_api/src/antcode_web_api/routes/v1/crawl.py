"""分布式爬取队列 REST API

提供批次管理、进度查询、测试执行和监控指标接口。

需求: 1.1, 1.2, 1.3, 1.4, 1.5, 6.4, 9.1, 9.2, 12.1, 12.3, 12.4
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from antcode_core.common.exceptions import BatchNotFoundError, BatchStateError
from antcode_web_api.response import Messages, page
from antcode_web_api.response import success as success_response
from antcode_core.common.security.auth import get_current_user
from antcode_core.domain.models.crawl import CrawlBatch
from antcode_core.domain.models.project import Project
from antcode_core.domain.schemas.common import BaseResponse, PaginationResponse
from antcode_core.domain.schemas.crawl import (
    AlertConfigResponse,
    AlertInfo,
    AlertsResponse,
    BatchMetricsResponse,
    BatchProgressResponse,
    CrawlBatchCreateRequest,
    CrawlBatchResponse,
    CrawlBatchTestRequest,
    CrawlMetricsResponse,
    CrawlTestResultResponse,
    MetricsSummaryResponse,
    QueueMetricsResponse,
    SystemMetricsInfo,
    TestStatusResponse,
)
from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.crawl.batch_service import crawl_batch_service
from antcode_core.application.services.crawl.metrics_service import crawl_metrics_service
from antcode_core.application.services.crawl.progress_service import crawl_progress_service
from antcode_core.application.services.crawl.queue_service import crawl_queue_service
from antcode_core.application.services.crawl.test_service import crawl_test_service

router = APIRouter()


# =============================================================================
# 辅助函数
# =============================================================================


async def _build_batch_response(
    batch: CrawlBatch,
    project_public_id: str | None = None,
    project_public_id_map: dict[int, str] | None = None,
) -> CrawlBatchResponse:
    """构建批次响应对象"""
    # 获取项目公开 ID
    if project_public_id is None and project_public_id_map is not None:
        project_public_id = project_public_id_map.get(batch.project_id)

    if project_public_id is None:
        project = await Project.filter(id=batch.project_id).only("public_id").first()
        project_public_id = project.public_id if project else str(batch.project_id)

    return CrawlBatchResponse(
        id=batch.public_id,
        project_id=project_public_id,
        name=batch.name,
        description=batch.description,
        seed_urls=batch.seed_urls,
        max_depth=batch.max_depth,
        max_pages=batch.max_pages,
        max_concurrency=batch.max_concurrency,
        request_delay=batch.request_delay,
        timeout=batch.timeout,
        max_retries=batch.max_retries,
        status=batch.status,
        is_test=batch.is_test,
        created_at=batch.created_at,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
    )


# =============================================================================
# 批次管理 API
# =============================================================================


@router.post(
    "/batches",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="创建爬取批次",
    description="创建新的爬取批次，包含种子 URL 和配置参数",
)
async def create_batch(
    request: CrawlBatchCreateRequest,
    current_user=Depends(get_current_user),
):
    """创建爬取批次

    需求: 1.1 - 用户提交创建批次请求时创建新批次并返回批次 ID
    """
    try:
        batch = await crawl_batch_service.create_batch(
            project_id=request.project_id,
            name=request.name,
            seed_urls=request.seed_urls,
            user_id=current_user.user_id,
            description=request.description,
            max_depth=request.max_depth,
            max_pages=request.max_pages,
            max_concurrency=request.max_concurrency,
            request_delay=request.request_delay,
            timeout=request.timeout,
            max_retries=request.max_retries,
            is_test=False,
        )

        response = await _build_batch_response(batch)
        return success_response(response, message=Messages.CREATED_SUCCESS)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"创建批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="创建批次失败",
        )


@router.get(
    "/batches/{batch_id}",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="获取批次详情",
    description="获取指定批次的详细信息",
)
async def get_batch(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """获取批次详情

    需求: 1.5 - 用户查询批次状态时返回批次的当前状态和进度信息
    """
    try:
        batch = await crawl_batch_service.get_batch(batch_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="批次不存在",
            )

        response = await _build_batch_response(batch)
        return success_response(response, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取批次详情失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取批次详情失败",
        )


@router.get(
    "/batches",
    response_model=PaginationResponse[CrawlBatchResponse],
    summary="获取批次列表",
    description="分页获取批次列表，支持按项目和状态筛选",
)
async def list_batches(
    project_id: str = Query(None, description="项目公开ID"),
    batch_status: str = Query(None, alias="status", description="批次状态"),
    is_test: bool = Query(None, description="是否为测试批次"),
    page_num: int = Query(1, ge=1, alias="page", description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user=Depends(get_current_user),
):
    """获取批次列表"""
    try:
        batches, total = await crawl_batch_service.list_batches(
            project_id=project_id,
            user_id=current_user.user_id,
            status=batch_status,
            is_test=is_test,
            page=page_num,
            size=size,
        )

        project_public_id_map = {}
        project_ids = list({b.project_id for b in batches})
        if project_ids:
            project_public_id_map = await QueryHelper.batch_get_project_public_ids(project_ids)

        items = [
            await _build_batch_response(b, project_public_id_map=project_public_id_map)
            for b in batches
        ]
        return page(items, total, page_num, size, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取批次列表失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取批次列表失败",
        )


@router.post(
    "/batches/{batch_id}/start",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="启动批次",
    description="启动指定批次的爬取任务",
)
async def start_batch(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """启动批次"""
    try:
        batch = await crawl_batch_service.start_batch(batch_id)
        response = await _build_batch_response(batch)
        return success_response(response, message="批次已启动")

    except BatchNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次不存在",
        )
    except BatchStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"启动批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="启动批次失败",
        )


@router.post(
    "/batches/{batch_id}/pause",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="暂停批次",
    description="暂停指定批次的爬取任务",
)
async def pause_batch(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """暂停批次

    需求: 1.2 - 用户请求暂停批次时停止分发新任务并保持当前进度
    """
    try:
        batch = await crawl_batch_service.pause_batch(batch_id)
        response = await _build_batch_response(batch)
        return success_response(response, message="批次已暂停")

    except BatchNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次不存在",
        )
    except BatchStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"暂停批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="暂停批次失败",
        )


@router.post(
    "/batches/{batch_id}/resume",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="恢复批次",
    description="恢复已暂停的批次",
)
async def resume_batch(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """恢复批次

    需求: 1.3 - 用户请求恢复批次时从暂停点继续分发任务
    """
    try:
        batch = await crawl_batch_service.resume_batch(batch_id)
        response = await _build_batch_response(batch)
        return success_response(response, message="批次已恢复")

    except BatchNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次不存在",
        )
    except BatchStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"恢复批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="恢复批次失败",
        )


@router.post(
    "/batches/{batch_id}/cancel",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="取消批次",
    description="取消指定批次并清理相关资源",
)
async def cancel_batch(
    batch_id: str,
    cleanup: bool = Query(True, description="是否清理队列和进度数据"),
    current_user=Depends(get_current_user),
):
    """取消批次

    需求: 1.4 - 用户请求取消批次时停止所有任务并清理相关资源
    """
    try:
        batch = await crawl_batch_service.cancel_batch(batch_id, cleanup=cleanup)
        response = await _build_batch_response(batch)
        return success_response(response, message="批次已取消")

    except BatchNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次不存在",
        )
    except BatchStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"取消批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="取消批次失败",
        )



# =============================================================================
# 进度查询 API
# =============================================================================


@router.get(
    "/batches/{batch_id}/progress",
    response_model=BaseResponse[BatchProgressResponse],
    summary="获取批次进度",
    description="获取指定批次的进度信息",
)
async def get_batch_progress(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """获取批次进度

    需求: 6.4 - 查询批次进度时返回 URL 统计、速度、活跃 Worker 数等信息
    """
    try:
        # 获取批次信息
        batch = await crawl_batch_service.get_batch(batch_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="批次不存在",
            )

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 获取进度信息
        progress = await crawl_progress_service.get_progress(
            project_id=project_public_id,
            batch_id=batch_id,
        )

        if progress:
            response = BatchProgressResponse(
                batch_id=batch_id,
                total_urls=progress.total_urls,
                pending_urls=progress.pending_urls,
                completed_urls=progress.completed_urls,
                failed_urls=progress.failed_urls,
                active_workers=progress.active_workers,
                speed_per_minute=progress.speed_per_minute,
                last_updated=progress.last_updated if progress.last_updated else None,
            )
        else:
            # 批次尚未启动，返回初始进度
            response = BatchProgressResponse(
                batch_id=batch_id,
                total_urls=len(batch.seed_urls),
                pending_urls=len(batch.seed_urls),
                completed_urls=0,
                failed_urls=0,
                active_workers=0,
                speed_per_minute=0.0,
                last_updated=None,
            )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取批次进度失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取批次进度失败",
        )


# =============================================================================
# 测试执行 API
# =============================================================================


@router.post(
    "/batches/test",
    response_model=BaseResponse[CrawlTestResultResponse],
    summary="执行测试爬取",
    description="创建并执行一个限制范围的测试批次",
)
async def execute_test(
    request: CrawlBatchTestRequest,
    current_user=Depends(get_current_user),
):
    """执行测试爬取

    需求: 12.1 - 用户请求测试执行时创建一个限制范围的测试批次
    需求: 12.3 - 测试执行完成时返回详细的执行结果和样本数据
    需求: 12.4 - 测试执行失败时返回具体的错误信息和失败原因
    """
    try:
        result = await crawl_test_service.execute_test(
            project_id=request.project_id,
            seed_urls=request.seed_urls,
            user_id=current_user.user_id,
            max_depth=request.max_depth,
            max_pages=request.max_pages,
        )

        response = CrawlTestResultResponse(
            batch_id=result.batch_id,
            success=result.success,
            total_pages=result.total_pages,
            success_pages=result.success_pages,
            failed_pages=result.failed_pages,
            sample_data=result.sample_data,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        message = "测试执行成功" if result.success else "测试执行失败"
        return success_response(response, message=message)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"执行测试失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="执行测试失败",
        )


@router.post(
    "/batches/test/create",
    response_model=BaseResponse[CrawlBatchResponse],
    summary="创建测试批次",
    description="仅创建测试批次，不立即执行",
)
async def create_test_batch(
    request: CrawlBatchTestRequest,
    current_user=Depends(get_current_user),
):
    """创建测试批次（不立即执行）"""
    try:
        batch = await crawl_test_service.create_test_batch(
            project_id=request.project_id,
            seed_urls=request.seed_urls,
            user_id=current_user.user_id,
            max_depth=request.max_depth,
            max_pages=request.max_pages,
        )

        response = await _build_batch_response(batch)
        return success_response(response, message="测试批次创建成功")

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"创建测试批次失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="创建测试批次失败",
        )


@router.get(
    "/batches/test/{batch_id}/status",
    response_model=BaseResponse[TestStatusResponse],
    summary="获取测试状态",
    description="获取测试批次的执行状态",
)
async def get_test_status(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """获取测试状态"""
    try:
        status_info = await crawl_test_service.get_test_status(batch_id)

        if status_info.get("status") == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="测试批次不存在",
            )

        response = TestStatusResponse(
            batch_id=status_info.get("batch_id", batch_id),
            status=status_info.get("status", "unknown"),
            started_at=status_info.get("started_at"),
            completed_at=status_info.get("completed_at"),
        )
        return success_response(response, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取测试状态失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取测试状态失败",
        )


@router.get(
    "/batches/test/{batch_id}/result",
    response_model=BaseResponse[CrawlTestResultResponse],
    summary="获取测试结果",
    description="获取测试批次的执行结果",
)
async def get_test_result(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """获取测试结果"""
    try:
        result = await crawl_test_service.get_test_result(batch_id)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="测试结果不存在",
            )

        response = CrawlTestResultResponse(
            batch_id=result.batch_id,
            success=result.success,
            total_pages=result.total_pages,
            success_pages=result.success_pages,
            failed_pages=result.failed_pages,
            sample_data=result.sample_data,
            errors=result.errors,
            duration_seconds=result.duration_seconds,
        )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取测试结果失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取测试结果失败",
        )


@router.delete(
    "/batches/test/{batch_id}",
    response_model=BaseResponse,
    summary="清理测试数据",
    description="清理测试批次相关的所有数据",
)
async def cleanup_test(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """清理测试数据"""
    try:
        success = await crawl_test_service.cleanup_test(batch_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="清理测试数据失败",
            )

        return success_response(None, message="测试数据已清理")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清理测试数据失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="清理测试数据失败",
        )


# =============================================================================
# 监控指标 API
# =============================================================================


@router.get(
    "/metrics",
    response_model=BaseResponse[CrawlMetricsResponse],
    summary="获取系统监控指标",
    description="获取爬取系统的整体监控指标",
)
async def get_system_metrics(
    project_id: str = Query(..., description="项目公开ID"),
    current_user=Depends(get_current_user),
):
    """获取系统监控指标

    需求: 9.1 - 查询系统指标时返回 Stream 长度、PEL 大小、去重集合大小等
    """
    try:
        # 使用 metrics_service 收集系统指标
        system_metrics = await crawl_metrics_service.collect_system_metrics(project_id)

        # 获取批次统计
        batches, total_batches = await crawl_batch_service.list_batches(
            project_id=project_id,
            page=1,
            size=1000,
        )

        running_batches = sum(1 for b in batches if b.status == "running")

        response = CrawlMetricsResponse(
            stream_length=system_metrics.total_stream_length,
            pel_size=system_metrics.total_pel_size,
            dedup_size=system_metrics.dedup_size,
            dead_letter_count=system_metrics.dead_letter_count,
            active_workers=system_metrics.active_workers,
            total_batches=total_batches,
            running_batches=running_batches,
        )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取系统监控指标失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取系统监控指标失败",
        )


@router.get(
    "/metrics/batch/{batch_id}",
    response_model=BaseResponse[BatchMetricsResponse],
    summary="获取批次监控指标",
    description="获取指定批次的监控指标",
)
async def get_batch_metrics(
    batch_id: str,
    current_user=Depends(get_current_user),
):
    """获取批次监控指标

    需求: 9.2 - 查询批次指标时返回完成数、失败数、速度、活跃 Worker 数等
    """
    try:
        # 获取批次信息
        batch = await crawl_batch_service.get_batch(batch_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="批次不存在",
            )

        # 获取项目公开 ID
        project = await Project.filter(id=batch.project_id).first()
        project_public_id = project.public_id if project else str(batch.project_id)

        # 获取进度信息
        progress = await crawl_progress_service.get_progress(
            project_id=project_public_id,
            batch_id=batch_id,
        )

        # 构建进度和配置字典
        progress_dict = {
            "total_urls": progress.total_urls if progress else len(batch.seed_urls),
            "completed_urls": progress.completed_urls if progress else 0,
            "failed_urls": progress.failed_urls if progress else 0,
            "pending_urls": progress.pending_urls if progress else len(batch.seed_urls),
            "speed_per_minute": progress.speed_per_minute if progress else 0.0,
            "active_workers": progress.active_workers if progress else 0,
        }

        config_dict = {
            "max_depth": batch.max_depth,
            "max_pages": batch.max_pages,
            "max_concurrency": batch.max_concurrency,
            "max_retries": batch.max_retries,
        }

        # 使用 metrics_service 收集批次指标
        batch_metrics = await crawl_metrics_service.collect_batch_metrics(
            project_id=project_public_id,
            batch_id=batch_id,
            batch_status=batch.status,
            progress=progress_dict,
            config=config_dict,
        )

        # 构建强类型响应
        response = BatchMetricsResponse(**batch_metrics.to_dict())
        return success_response(response, message=Messages.QUERY_SUCCESS)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取批次监控指标失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取批次监控指标失败",
        )


@router.get(
    "/metrics/queues/{project_id}",
    response_model=BaseResponse[QueueMetricsResponse],
    summary="获取队列详细指标",
    description="获取指定项目的队列详细指标",
)
async def get_queue_metrics(
    project_id: str,
    current_user=Depends(get_current_user),
):
    """获取队列详细指标"""
    try:
        # 获取队列统计
        queue_stats = await crawl_queue_service.get_queue_stats(project_id)

        # 获取消费者统计
        consumer_stats = await crawl_queue_service.get_consumer_stats(project_id)

        response = QueueMetricsResponse(
            project_id=project_id,
            queue_stats=queue_stats,
            consumer_stats=consumer_stats,
        )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取队列详细指标失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取队列详细指标失败",
        )


@router.get(
    "/metrics/alerts/{project_id}",
    response_model=BaseResponse[AlertsResponse],
    summary="检测告警",
    description="检测指定项目的监控指标是否超过阈值",
)
async def check_alerts(
    project_id: str,
    current_user=Depends(get_current_user),
):
    """检测告警

    需求: 9.3 - 指标超过阈值时记录告警日志
    """
    try:
        # 检测告警
        alerts = await crawl_metrics_service.check_alerts(project_id)

        # 转换为强类型响应
        alert_items = [
            AlertInfo(
                level=a.level,
                metric_name=a.metric_name,
                current_value=a.current_value,
                threshold=a.threshold,
                message=a.message,
                project_id=a.project_id,
                created_at=a.created_at,
            )
            for a in alerts
        ]

        response = AlertsResponse(
            project_id=project_id,
            alerts=alert_items,
            alert_count=len(alerts),
            has_critical_alerts=any(a.level == "critical" for a in alerts),
        )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"检测告警失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="检测告警失败",
        )


@router.get(
    "/metrics/summary/{project_id}",
    response_model=BaseResponse[MetricsSummaryResponse],
    summary="获取指标汇总",
    description="获取指定项目的指标汇总（包含指标和告警）",
)
async def get_metrics_summary(
    project_id: str,
    current_user=Depends(get_current_user),
):
    """获取指标汇总"""
    try:
        summary = await crawl_metrics_service.get_metrics_summary(project_id)

        # 转换为强类型响应
        metrics_info = SystemMetricsInfo(**summary["metrics"])
        alert_items = [AlertInfo(**a) for a in summary["alerts"]]

        response = MetricsSummaryResponse(
            metrics=metrics_info,
            alerts=alert_items,
            alert_count=summary["alert_count"],
            has_critical_alerts=summary["has_critical_alerts"],
        )

        return success_response(response, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取指标汇总失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取指标汇总失败",
        )


@router.get(
    "/metrics/config",
    response_model=BaseResponse[AlertConfigResponse],
    summary="获取告警配置",
    description="获取当前的告警阈值配置",
)
async def get_alert_config(
    current_user=Depends(get_current_user),
):
    """获取告警配置"""
    try:
        config = crawl_metrics_service.get_alert_config()
        response = AlertConfigResponse(**config)
        return success_response(response, message=Messages.QUERY_SUCCESS)

    except Exception as e:
        logger.error(f"获取告警配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取告警配置失败",
        )


@router.put(
    "/metrics/config",
    response_model=BaseResponse[AlertConfigResponse],
    summary="更新告警配置",
    description="更新告警阈值配置",
)
async def update_alert_config(
    stream_length_threshold: int = Query(None, description="Stream长度告警阈值"),
    pel_size_threshold: int = Query(None, description="PEL大小告警阈值"),
    dead_letter_threshold: int = Query(None, description="死信队列告警阈值"),
    dedup_size_threshold: int = Query(None, description="去重集合大小告警阈值"),
    current_user=Depends(get_current_user),
):
    """更新告警配置"""
    try:
        crawl_metrics_service.update_alert_config(
            stream_length_threshold=stream_length_threshold,
            pel_size_threshold=pel_size_threshold,
            dead_letter_threshold=dead_letter_threshold,
            dedup_size_threshold=dedup_size_threshold,
        )

        config = crawl_metrics_service.get_alert_config()
        response = AlertConfigResponse(**config)
        return success_response(response, message="告警配置已更新")

    except Exception as e:
        logger.error(f"更新告警配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新告警配置失败",
        )
