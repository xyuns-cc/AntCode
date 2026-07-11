"""任务运行接口"""

import json
from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.scheduler.scheduler_service import scheduler_service
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.domain.schemas.common import BaseResponse
from antcode_core.domain.schemas.task import TaskRunResponse
from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore
from antcode_core.infrastructure.redis import build_cancel_control_payload, control_stream
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response

runs_router = APIRouter()
_CANCELLABLE_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)
_UNASSIGNED_CANCELLABLE_STATUSES = (TaskStatus.PENDING, TaskStatus.QUEUED)


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
    except Exception as e:
        logger.error(f"获取执行详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取执行详情失败")


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

    cancelled, send_error = await _send_worker_cancel(execution, current_user.user_id)
    if execution.worker_id and not cancelled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"取消指令发送失败，请重试：{send_error or '未知错误'}",
        )

    await _mark_execution_cancelled(execution.run_id, current_user.user_id)
    logger.info(f"执行已取消: {run_id}, 远程取消={cancelled}")
    return _cancel_success(run_id, remote_cancelled=cancelled)


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
    updated = await TaskRun.filter(
        run_id=execution.run_id,
        worker_id__isnull=True,
        status__in=list(_UNASSIGNED_CANCELLABLE_STATUSES),
    ).update(
        status=TaskStatus.CANCELLED,
        end_time=datetime.now(UTC),
        error_message=f"用户取消 (user_id={user_id})",
    )
    return bool(updated)


async def _send_worker_cancel(execution: TaskRun, user_id: int) -> tuple[bool, str | None]:
    if not execution.worker_id:
        return False, None
    try:
        await _write_worker_cancel_event(execution, user_id)
        return True, None
    except Exception as exc:
        logger.warning(f"发送取消指令失败: {exc}")
        return False, str(exc)


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


async def _mark_execution_cancelled(run_id: str, user_id: int) -> None:
    from antcode_core.application.services.scheduler.execution_status_service import execution_status_service

    await execution_status_service.update_runtime_status(
        run_id=run_id,
        status="cancelled",
        status_at=datetime.now(UTC),
        error_message=f"用户取消 (user_id={user_id})",
    )


def _cancel_success(run_id: str, *, remote_cancelled: bool) -> BaseResponse[dict]:
    return success_response(
        {"run_id": run_id, "status": "cancelled", "remote_cancelled": remote_cancelled},
        message="任务已取消",
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


@runs_router.get("/{run_id}/artifacts/{artifact_name}/download")
async def download_run_artifact(
    run_id: str,
    artifact_name: str,
    current_user: TokenData = Depends(get_current_user),
):
    """按 name 下载单个 artifact；权限校验后从 PG blob store 读原始 bytes。"""
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
    try:
        content = await store.read_blob(content_hash)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"读取 artifact blob 失败: run_id={run_id} name={artifact_name}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"下载失败: {exc}",
        ) from exc

    mime_type = artifact.get("mime_type") or "application/octet-stream"

    # 用 iterable 包一层做 StreamingResponse，避免整块 bytes 长期驻留
    def _iter():
        chunk_size = 64 * 1024
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]

    safe_name = artifact_name.replace('"', "'")
    return StreamingResponse(
        _iter(),
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(content)),
        },
    )


# ---------------------------------------------------------------------------
# O2: 爬虫数据链闭环
#
# worker 端 RuleSpider / Scrapy 通过 SpiderDataReporter 把抓到的 items 写到
# Redis stream ``spider:data:{run_id}``。此处对外暴露列表端点，让前端"抓取
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
    raw, note = await _read_spider_stream(run_id, start_id, count)
    items, last_id = _decode_spider_items(raw, start_id)
    data = {"items": items, "last_id": last_id, "count": len(items)}
    if note:
        data["note"] = note
    return success_response(data, message=Messages.QUERY_SUCCESS)


async def _read_spider_stream(run_id: str, start_id: str, count: int) -> tuple[list[Any], str | None]:
    try:
        from antcode_core.common.config import settings as _settings
        from antcode_core.infrastructure.redis import get_redis_client
        from antcode_core.infrastructure.redis.keys import RedisKeys

        redis = await get_redis_client()
        if redis is None:
            return [], "Redis 不可用"
        keys = RedisKeys(namespace=_settings.REDIS_NAMESPACE)
        stream_key = keys.spider_data_stream(run_id)
        min_id = f"({start_id}" if start_id and start_id != "0" else "-"
        raw = await redis.xrange(stream_key, min=min_id, max="+", count=count)
    except Exception as exc:
        logger.warning(f"读取 spider items 失败: run_id={run_id} err={exc}")
        return [], None
    return list(raw or []), None


def _decode_spider_items(raw: list[Any], start_id: str) -> tuple[list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    last_id: str = start_id
    for msg_id, fields in raw or []:
        decoded_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        last_id = decoded_id
        row = {"_id": decoded_id, **_decode_spider_fields(fields)}
        items.append(row)
    return items, last_id


def _decode_spider_fields(fields: dict[Any, Any] | None) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for raw_key, raw_value in (fields or {}).items():
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        decoded[key] = _decode_spider_value(key, value)
    return decoded


def _decode_spider_value(key: str, value: Any) -> Any:
    if key != "data" or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


router = runs_router

__all__ = ["runs_router", "router"]
