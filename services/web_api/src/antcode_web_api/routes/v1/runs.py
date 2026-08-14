"""任务运行接口。

已分配运行只记录取消请求并通知 Worker。
最终状态必须来自 Worker 的真实结算结果。
取消请求持久化必须先于控制事件发送。
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.runtime_paths import ensure_runtime_dir
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import TaskRunResponse
from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore
from antcode_core.infrastructure.redis import build_cancel_control_payload, control_stream
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from loguru import logger
from starlette.background import BackgroundTask

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response
from antcode_web_api.routes.v1 import run_cancel_support as _cancel_support
from antcode_web_api.services.run_spider_items import (
    decode_spider_items as _decode_spider_items,
)
from antcode_web_api.services.run_spider_items import read_spider_stream as _read_spider_stream

_cancel_success = _cancel_support.cancel_success
_record_assigned_cancel_request = _cancel_support.record_assigned_cancel_request

runs_router = APIRouter()
_CANCELLABLE_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)
# P1-FN-01: DISPATCHING+worker_id=NULL 空绑窗口也纳入 CAS 取消区间，交 cancel_unassigned_run 收敛 dispatch_status，防"API 显示已取消但仍真实执行"。
_UNASSIGNED_CANCELLABLE_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.DISPATCHING)


@runs_router.get("/{run_id}", response_model=BaseResponse[TaskRunResponse])
async def get_run(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取执行详情"""
    try:
        execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        return success_response(TaskRunResponse.from_orm(execution), message=Messages.QUERY_SUCCESS)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("获取执行详情失败: run_id={}", run_id)
        raise HTTPException(status_code=500, detail="获取执行详情失败") from exc


@runs_router.post("/{run_id}/cancel", response_model=BaseResponse[dict])
async def cancel_run(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """
    取消正在执行的任务

    - 如果任务在 Worker 上运行，会发送取消指令到 Worker
    - 如果任务在队列中等待，会使用 CAS UPDATE 抢占式标记为已取消（T5）
    """
    execution = await _get_cancellable_execution(run_id, current_user.user_id)
    if _is_unassigned_execution(execution):
        if await _cancel_unassigned_execution(execution, current_user.user_id):
            logger.info(f"执行已取消 (CAS): {run_id}")
            return _cancel_success(run_id, remote_cancelled=False)
        execution = await _get_execution(run_id, current_user.user_id)

    if execution.worker_id:
        if not await _record_assigned_cancel_request(execution.run_id, current_user.user_id):
            await _raise_if_terminal_conflict(run_id, current_user.user_id)
            return _cancel_success(run_id, remote_cancelled=False)
        cancelled = await _send_worker_cancel(execution, current_user.user_id)
        if not cancelled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="取消指令发送失败，请稍后重试",
            )
        return _cancel_success(run_id, remote_cancelled=True)

    # P1-FN-02: 状态机 CAS 结果必须被尊重 —— 完成结果抢先落终态时不能
    # 对外谎报 cancelled。
    marked = await _mark_execution_cancelled(execution.run_id, current_user.user_id)
    if not marked:
        await _raise_if_terminal_conflict(run_id, current_user.user_id)
    logger.info(f"执行已取消: {run_id}, 远程取消=False, 状态标记={marked}")
    return _cancel_success(run_id, remote_cancelled=False)


async def _get_execution(run_id: str, user_id: int) -> TaskRun:
    execution = await scheduler_service.get_execution_with_permission(run_id, user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    return execution


async def _get_cancellable_execution(run_id: str, user_id: int) -> TaskRun:
    from antcode_core.domain.models.task import Task

    execution = await _get_execution(run_id, user_id)
    if execution.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(status_code=400, detail=f"任务状态为 {execution.status.value}，无法取消")
    if not await Task.get_or_none(id=execution.task_id):
        raise HTTPException(status_code=404, detail="关联任务不存在")
    return execution


def _is_unassigned_execution(execution: TaskRun) -> bool:
    return execution.worker_id is None and execution.status in _UNASSIGNED_CANCELLABLE_STATUSES


async def _cancel_unassigned_execution(execution: TaskRun, user_id: int) -> bool:
    # P1-FN-02: 委托核心服务 —— 同步收敛 dispatch_status 并回写 Task 状态，
    # 阻止 Master 派发已取消的 run。
    from antcode_core.application.services.scheduler.execution_status_service import execution_status_service

    return await execution_status_service.cancel_unassigned_run(execution.run_id, user_id)


async def _raise_if_terminal_conflict(run_id: str, user_id: int) -> None:
    """runtime CAS 未生效时的诚实回应。

    - 已是 CANCELLED → 幂等成功（不抛）；
    - 已进入其它终态 → 409，返回真实状态；
    - 仍是非终态 → 取消指令已发出，最终状态由 Worker 结果决定（不抛）。
    """
    final = await _get_execution(run_id, user_id)
    final_status = final.status.value if final.status else "unknown"
    terminal = {"success", "failed", "timeout", "skipped", "rejected"}
    if final_status in terminal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务已进入终态({final_status})，取消未生效",
        )


async def _send_worker_cancel(execution: TaskRun, user_id: int) -> bool:
    return await _cancel_support.send_worker_cancel(execution, user_id, _write_worker_cancel_event)


async def _write_worker_cancel_event(execution: TaskRun, user_id: int) -> None:
    from antcode_core.application.services.runtime.runtime_control_service import write_control_event
    from antcode_core.application.services.workers.worker_service import worker_service
    from antcode_core.infrastructure.redis import get_redis_client

    worker = await worker_service.get_worker_by_id(execution.worker_id)
    if not worker:
        raise LookupError("worker 不存在")
    redis = await get_redis_client()
    payload = build_cancel_control_payload(run_id=execution.run_id, reason=f"user_cancel:{user_id}")
    await write_control_event(redis, control_stream(worker.public_id), payload)
    logger.info(f"已发送取消指令到 Worker: {worker.name}")


async def _mark_execution_cancelled(run_id: str, user_id: int) -> bool:
    from antcode_core.application.services.scheduler.execution_status_service import execution_status_service

    return bool(
        await execution_status_service.update_runtime_status(
            run_id=run_id,
            status="cancelled",
            status_at=datetime.now(UTC),
            error_message=f"用户取消 (user_id={user_id})",
        )
    )


@runs_router.post("/{run_id}/stop", response_model=BaseResponse[dict])
async def stop_run_alias(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """前端兼容路径 — 转发到 cancel_run，使两个端点行为一致"""
    return await cancel_run(run_id=run_id, current_user=current_user)


# ---------------------------------------------------------------------------
# L2: 产物链闭环
#
# worker 收集产物 → 写 PG blob → refs 塞进 TaskResult.data["artifacts"] →
# proto TaskStatus.data (map<string,string>, 非字符串走 json.dumps) →
# master result_loop → TaskRun.result_data["artifacts"] (JSON 字符串)。
# 此处对外暴露：list / download，让用户能看到并下载自己的产物。
# 权限：复用 scheduler_service.get_execution_with_permission。
# ---------------------------------------------------------------------------


def _parse_artifacts(result_data: Any) -> list[dict[str, Any]]:
    """从 ``TaskRun.result_data['artifacts']`` 解析出 artifact 列表。

    经过 proto map<string,string> 中转后，非字符串值会被 ``json.dumps``
    序列化为字符串，此处再反序列化回 list[dict]。
    """
    if not isinstance(result_data, dict):
        return []
    raw = result_data.get("artifacts")
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(decoded, list):
            return [item for item in decoded if isinstance(item, dict)]
    return []


def _sanitize_artifact_public(artifact: dict[str, Any]) -> dict[str, Any]:
    """只暴露给用户看的字段，不外泄 pgartifact:// URI 内部结构。"""
    return {
        "name": artifact.get("name", ""),
        "artifact_type": artifact.get("artifact_type", "file"),
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "checksum": artifact.get("checksum") or "",
        "mime_type": artifact.get("mime_type") or "application/octet-stream",
        "created_at": artifact.get("created_at"),
    }


@runs_router.get("/{run_id}/artifacts", response_model=BaseResponse[dict])
async def list_run_artifacts(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """列出某次执行产出的所有 artifacts（不含下载 URI，敏感字段脱敏）。"""
    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    artifacts = _parse_artifacts(execution.result_data)
    return success_response(
        {"items": [_sanitize_artifact_public(a) for a in artifacts]},
        message=Messages.QUERY_SUCCESS,
    )


def _match_artifact_by_name(artifacts: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for artifact in artifacts:
        if artifact.get("name") == name:
            return artifact
    return None


def _extract_content_hash(uri: str) -> str | None:
    """``pgartifact://<sha256>`` → ``<sha256>``。"""
    if not isinstance(uri, str) or not uri.startswith("pgartifact://"):
        return None
    return uri[len("pgartifact://") :].strip() or None


@runs_router.get("/{run_id}/artifacts/{artifact_name:path}/download")
async def download_run_artifact(
    run_id: str,
    artifact_name: str,
    current_user: TokenData = Depends(get_current_user),
):
    """按 name 下载单个 artifact；权限校验后从 PG blob store 读原始 bytes。

    P2-05：artifact name 允许含斜杠（如 ``reports/2026/result.json``）。
    路由用 ``:path`` 转换器捕获整段子路径；不再因单路径段限制而 404。
    同时下载改为按 chunk 流式，避免大文件全量常驻内存。
    """
    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    artifacts = _parse_artifacts(execution.result_data)
    artifact = _match_artifact_by_name(artifacts, artifact_name)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产物不存在: {artifact_name}")

    content_hash = _extract_content_hash(artifact.get("uri", ""))
    if not content_hash:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="产物 URI 不合法或已下线")

    store = PostgresArtifactStore()
    download_dir = ensure_runtime_dir("web_api", "tmp", "artifact-downloads")
    file_descriptor, temp_name = tempfile.mkstemp(prefix="artifact-", dir=download_dir)
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        await store.read_blob_to_file(content_hash, temp_path)
    except FileNotFoundError as exc:
        temp_path.unlink(missing_ok=True)
        logger.exception("artifact blob 不存在: run_id={} name={}", run_id, artifact_name)
        raise HTTPException(status_code=404, detail="产物不存在") from exc
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        logger.exception("读取 artifact blob 失败: run_id={} name={}", run_id, artifact_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="产物下载失败",
        ) from exc

    mime_type = artifact.get("mime_type") or "application/octet-stream"

    # 只用 basename 作为下载文件名（不含目录路径），且转义引号
    safe_basename = artifact_name.rsplit("/", 1)[-1].replace('"', "'")
    return FileResponse(
        temp_path,
        media_type=mime_type,
        filename=safe_basename,
        background=BackgroundTask(temp_path.unlink, missing_ok=True),
    )


# ---------------------------------------------------------------------------
# O2: 爬虫数据链闭环
#
# worker 端 RuleSpider / Scrapy 通过 SpiderDataReporter 把抓到的 items 写到
# Redis stream ``{namespace}:spider:<run_id>:data``。此处对外暴露列表端点，让前端"抓取
# 数据"tab 能看到 items。权限走 ``scheduler_service.get_execution_with_permission``。
# ---------------------------------------------------------------------------


@runs_router.get("/{run_id}/spider-items", response_model=BaseResponse[dict])
async def list_spider_items(
    run_id: str,
    current_user: TokenData = Depends(get_current_user),
    start_id: str = Query("0", description="Redis stream 起始 id，分页用"),
    count: int = Query(100, ge=1, le=1000, description="每页数量"),
):
    """列出该 run 由 SpiderDataReporter 写入的 items（按时序）。"""
    execution = await scheduler_service.get_execution_with_permission(run_id, current_user.user_id)
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    try:
        raw = await _read_spider_stream(run_id, start_id, count + 1)
    except Exception as exc:
        logger.exception(f"读取 spider items 失败: run_id={run_id}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="抓取数据存储暂不可用",
        ) from exc
    has_more = len(raw) > count
    items, last_id = _decode_spider_items(raw[:count], start_id)
    data = {"items": items, "last_id": last_id, "count": len(items), "has_more": has_more}
    return success_response(data, message=Messages.QUERY_SUCCESS)


router = runs_router

__all__ = ["runs_router", "router"]
