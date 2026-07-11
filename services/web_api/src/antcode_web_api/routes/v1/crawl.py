"""分布式爬取队列 REST API

提供批次管理、进度查询、测试执行和监控指标接口。

需求: 1.1, 1.2, 1.3, 1.4, 1.5, 6.4, 9.1, 9.2, 12.1, 12.3, 12.4
"""

from typing import Any

from antcode_core.application.services.base import QueryHelper
from antcode_core.application.services.crawl.batch_service import crawl_batch_service
from antcode_core.application.services.crawl.metrics_service import crawl_metrics_service
from antcode_core.application.services.crawl.progress_service import crawl_progress_service
from antcode_core.application.services.crawl.queue_service import crawl_queue_service
from antcode_core.application.services.crawl.test_service import crawl_test_service
from antcode_core.common.exceptions import BatchNotFoundError, BatchStateError
from antcode_core.common.security.auth import TokenData, get_current_admin_user, get_current_user
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
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.response import Messages, page
from antcode_web_api.response import success as success_response

router = APIRouter()


# =============================================================================
# 权限校验辅助
# =============================================================================


async def _verify_batch_owner(batch_id: str, current_user: TokenData) -> CrawlBatch:
    """校验批次所有权

    管理员可访问任意批次；普通用户只能访问自己创建的批次。
    Raise 404 当批次不存在，403 当无权访问。
    """
    batch = await crawl_batch_service.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    if not getattr(current_user, "is_admin", False) and batch.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该批次")
    return batch


async def _verify_project_access(project_public_id: str, current_user: TokenData) -> Project:
    """R1-P2-1 (审查报告)：项目所有权校验。

    ``batches/*``（含 test / metrics 系列）此前都只查项目存在性、不查 owner，
    任意登录用户能对他人项目建批次、读他人项目指标。这里补上：普通用户仅
    可访问 ``user_id == current_user.user_id`` 的项目，管理员放行。项目
    不存在或无权访问都统一返 404（避免 IDOR 探测）。
    """
    if not project_public_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project_id 不能为空")
    project = await Project.get_or_none(public_id=str(project_public_id))
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if not getattr(current_user, "is_admin", False) and getattr(project, "user_id", None) != current_user.user_id:
        # 与其它端点一致：无权也返 404 而非 403，避免暴露资源存在
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(request.project_id, current_user)
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
    current_user: TokenData = Depends(get_current_user),
):
    """获取批次详情

    需求: 1.5 - 用户查询批次状态时返回批次的当前状态和进度信息
    """
    try:
        batch = await _verify_batch_owner(batch_id, current_user)

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
    current_user: TokenData = Depends(get_current_user),
):
    """获取批次列表"""
    try:
        # 管理员可以查看所有用户的批次；普通用户仅限自己的
        user_id_filter = None if getattr(current_user, "is_admin", False) else current_user.user_id
        batches, total = await crawl_batch_service.list_batches(
            project_id=project_id,
            user_id=user_id_filter,
            status=batch_status,
            is_test=is_test,
            page=page_num,
            size=size,
        )

        project_public_id_map = {}
        project_ids = list({b.project_id for b in batches})
        if project_ids:
            project_public_id_map = await QueryHelper.batch_get_project_public_ids(project_ids)

        items = [await _build_batch_response(b, project_public_id_map=project_public_id_map) for b in batches]
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
    current_user: TokenData = Depends(get_current_user),
):
    """启动批次"""
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    except HTTPException:
        raise
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
    current_user: TokenData = Depends(get_current_user),
):
    """暂停批次

    需求: 1.2 - 用户请求暂停批次时停止分发新任务并保持当前进度
    """
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    except HTTPException:
        raise
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
    current_user: TokenData = Depends(get_current_user),
):
    """恢复批次

    需求: 1.3 - 用户请求恢复批次时从暂停点继续分发任务
    """
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    except HTTPException:
        raise
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
    current_user: TokenData = Depends(get_current_user),
):
    """取消批次

    需求: 1.4 - 用户请求取消批次时停止所有任务并清理相关资源
    """
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    except HTTPException:
        raise
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
    current_user: TokenData = Depends(get_current_user),
):
    """获取批次进度

    需求: 6.4 - 查询批次进度时返回 URL 统计、速度、活跃 Worker 数等信息
    """
    try:
        # 获取批次信息（带所有权校验）
        batch = await _verify_batch_owner(batch_id, current_user)

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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(request.project_id, current_user)
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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(request.project_id, current_user)
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
    current_user: TokenData = Depends(get_current_user),
):
    """获取测试状态"""
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    current_user: TokenData = Depends(get_current_user),
):
    """获取测试结果"""
    try:
        await _verify_batch_owner(batch_id, current_user)
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
    current_user: TokenData = Depends(get_current_user),
):
    """清理测试数据"""
    try:
        await _verify_batch_owner(batch_id, current_user)
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
# R1-P2-28: 批次维度数据聚合 + 导出（产品闭环）
# =============================================================================


def _decode_stream_entry(entry_id: Any, data: dict, run_id: str) -> tuple | None:
    """把一条 xrange 返回的 stream entry 解成外部 yield tuple；失败返回 None。"""
    import json as _json

    try:
        raw_data = data.get(b"data") or data.get("data") or b"{}"
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8", errors="ignore")
        payload = _json.loads(raw_data)
        url_val = data.get(b"url") or data.get("url") or ""
        if isinstance(url_val, bytes):
            url_val = url_val.decode("utf-8", errors="ignore")
        ts_val = data.get(b"timestamp") or data.get("timestamp") or ""
        if isinstance(ts_val, bytes):
            ts_val = ts_val.decode("utf-8", errors="ignore")
        seq_val = data.get(b"sequence") or data.get("sequence") or "0"
        if isinstance(seq_val, bytes):
            seq_val = seq_val.decode("utf-8", errors="ignore")
        return (int(seq_val or 0), payload, str(url_val), str(ts_val), run_id)
    except Exception:
        return None


async def _iter_batch_items(batch_id: str, limit: int | None = None):
    """按批次 → 所有 run → 汇总 spider:data stream items。

    T7-B4b (P1-3): 原实现每个 run 串行发 XRANGE，导出 limit=10000 时上千次
    Redis RTT。改为**并发桶** —— 每 8 个 run 一组 ``asyncio.gather`` 并发
    发 XRANGE，单 run 内页数多时仍串行分页。默认每 stream count=1000（比原
    来 200 大 5x），减少大 stream 的分页次数。

    产物形式：``(seq, item_dict, url, timestamp, run_id)`` 生成器。
    """
    import asyncio as _asyncio

    from antcode_core.infrastructure.redis.client import get_redis_client
    from antcode_core.infrastructure.redis.keys import RedisKeys
    from tortoise import Tortoise

    keys = RedisKeys()
    client = await get_redis_client()
    # Tortoise Model.raw(sql, using_db) 只接受 SQL 字符串和可选连接对象,
    # 第二位参不是查询参数;必须走底层 conn.execute_query_dict 才能绑定 $1。
    conn = Tortoise.get_connection("default")
    runs = await conn.execute_query_dict(
        "SELECT run_id FROM task_executions WHERE result_data->>'crawl_batch_id' = $1 ORDER BY id ASC",
        [batch_id],
    )
    if not runs:
        return

    CONCURRENCY = 8
    PAGE_SIZE = 1000
    yielded = 0

    async def _read_run(run_id: str) -> list:
        """把单个 run 的 stream 全部拉下来（分页）。"""
        stream_key = keys.spider_data_stream(run_id)
        collected: list = []
        cursor = "-"
        while True:
            page = await client.xrange(stream_key, min=cursor, max="+", count=PAGE_SIZE)
            if not page:
                break
            collected.extend(page)
            if len(page) < PAGE_SIZE:
                break  # 已到尾
            last_id = page[-1][0]
            if isinstance(last_id, bytes):
                last_id = last_id.decode("utf-8", errors="ignore")
            cursor = f"({last_id}"
        return collected

    # 保持 run 之间的输出顺序（TaskRun.id ASC）；桶内并发但按原序 flush。
    for i in range(0, len(runs), CONCURRENCY):
        bucket = runs[i : i + CONCURRENCY]
        results = await _asyncio.gather(*(_read_run(r["run_id"]) for r in bucket), return_exceptions=True)
        for run, entries in zip(bucket, results, strict=False):
            if isinstance(entries, BaseException):
                continue
            for entry_id, data in entries:
                if limit is not None and yielded >= limit:
                    return
                item = _decode_stream_entry(entry_id, data, run["run_id"])
                if item is None:
                    continue
                yielded += 1
                yield item


@router.get(
    "/batches/{batch_id}/items",
    summary="批次维度聚合抓取数据",
    description="跨批次内所有 run 汇总 spider:data，按 sequence 排序返回",
)
async def get_batch_items(
    batch_id: str,
    limit: int = Query(100, ge=1, le=1000),
    current_user: TokenData = Depends(get_current_user),
):
    """P2-28: 补按批次聚合视图。用户之前只能一个 run 一个 run 翻，
    批次结果没有聚合入口。"""
    await _verify_batch_owner(batch_id, current_user)
    items = []
    async for seq, payload, url, ts, run_id in _iter_batch_items(batch_id, limit=limit):
        items.append(
            {
                "sequence": seq,
                "url": url,
                "timestamp": ts,
                "run_id": run_id,
                "data": payload,
            }
        )
    return success_response({"batch_id": batch_id, "items": items, "count": len(items)})


@router.get(
    "/batches/{batch_id}/export",
    summary="导出批次抓取数据",
    description="按 format 导出 JSON 或 CSV；数据流式返回，避免一次性加载大批",
)
async def export_batch(
    batch_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    limit: int = Query(10000, ge=1, le=100000),
    current_user: TokenData = Depends(get_current_user),
):
    """P2-28: 数据导出（CSV/JSON），流式响应。"""
    import csv as _csv
    import io as _io
    import json as _json

    await _verify_batch_owner(batch_id, current_user)

    if format == "json":

        async def _json_stream():
            yield '{"batch_id": "' + batch_id + '", "items": ['
            first = True
            async for seq, payload, url, ts, run_id in _iter_batch_items(batch_id, limit=limit):
                if not first:
                    yield ","
                first = False
                yield _json.dumps(
                    {"sequence": seq, "url": url, "timestamp": ts, "run_id": run_id, "data": payload},
                    ensure_ascii=False,
                )
            yield "]}"

        return StreamingResponse(
            _json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="batch-{batch_id[:8]}.json"'},
        )

    # CSV：字段先看第一条 item 决定列头
    async def _csv_stream():
        # 先取一条来确定列头
        columns: list[str] | None = None
        buf = _io.StringIO()
        writer = _csv.writer(buf)
        async for seq, payload, url, ts, run_id in _iter_batch_items(batch_id, limit=limit):
            data_keys = list(payload.keys()) if isinstance(payload, dict) else []
            if columns is None:
                columns = ["sequence", "url", "timestamp", "run_id"] + data_keys
                writer.writerow(columns)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
            row = [seq, url, ts, run_id]
            for k in columns[4:]:
                v = payload.get(k) if isinstance(payload, dict) else ""
                # 复合值 JSON 序列化
                if isinstance(v, (dict, list)):
                    v = _json.dumps(v, ensure_ascii=False)
                row.append(v if v is not None else "")
            writer.writerow(row)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    return StreamingResponse(
        _csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="batch-{batch_id[:8]}.csv"'},
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
    # P0-a2: 与 queues/alerts/summary 保持一致的项目所有权校验，
    # 避免任意登录用户传他人 project_id 读他人系统指标（IDOR）。
    await _verify_project_access(project_id, current_user)
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
    current_user: TokenData = Depends(get_current_user),
):
    """获取批次监控指标

    需求: 9.2 - 查询批次指标时返回完成数、失败数、速度、活跃 Worker 数等
    """
    try:
        # 获取批次信息（带所有权校验）
        batch = await _verify_batch_owner(batch_id, current_user)

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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(project_id, current_user)
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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(project_id, current_user)
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
    # R1-P2-1: 项目所有权校验
    await _verify_project_access(project_id, current_user)
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
    current_user: TokenData = Depends(get_current_admin_user),
):
    """更新告警配置（仅管理员）"""
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
