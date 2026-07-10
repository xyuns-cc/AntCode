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
from antcode_core.infrastructure.redis import build_cancel_control_payload, control_stream
from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger
from tortoise.exceptions import DoesNotExist

from antcode_web_api.response import Messages
from antcode_web_api.response import success as success_response

runs_router = APIRouter()


@runs_router.get("/{run_id}", response_model=BaseResponse[TaskRunResponse])
async def get_run(run_id: str, current_user: TokenData = Depends(get_current_user)):
    """获取执行详情"""
    try:
        execution = await scheduler_service.get_execution_with_permission(
            run_id, current_user.user_id
        )
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

        return success_response(
            TaskRunResponse.from_orm(execution), message=Messages.QUERY_SUCCESS
        )
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
    from antcode_core.domain.models.task import Task

    # 获取执行记录
    execution = await scheduler_service.get_execution_with_permission(
        run_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    # 检查状态
    if execution.status not in (
        TaskStatus.PENDING,
        TaskStatus.DISPATCHING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    ):
        raise HTTPException(
            status_code=400, detail=f"任务状态为 {execution.status.value}，无法取消"
        )

    # 获取任务信息
    task = await Task.get_or_none(id=execution.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="关联任务不存在")

    # T5: 未分发的执行使用 CAS UPDATE 抢占式置为 CANCELLED
    if execution.worker_id is None and execution.status in (
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
    ):
        updated = await TaskRun.filter(
            run_id=execution.run_id,
            worker_id__isnull=True,
            status__in=[TaskStatus.PENDING, TaskStatus.QUEUED],
        ).update(
            status=TaskStatus.CANCELLED,
            end_time=datetime.now(UTC),
            error_message=f"用户取消 (user_id={current_user.user_id})",
        )
        if updated:
            logger.info(f"执行已取消 (CAS): {run_id}")
            return success_response(
                {
                    "run_id": run_id,
                    "status": "cancelled",
                    "remote_cancelled": False,
                },
                message="任务已取消",
            )
        # 0 行命中：在 CAS 期间已被 dispatch，重新加载 execution
        execution = await scheduler_service.get_execution_with_permission(
            run_id, current_user.user_id
        )
        if not execution:
            raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    cancelled = False
    send_error: str | None = None

    # 如果任务正在 Worker 上运行，发送取消指令
    if execution.worker_id:
        try:
            from antcode_core.application.services.runtime.runtime_control_service import (
                write_control_event,
            )
            from antcode_core.application.services.workers.worker_service import (
                worker_service,
            )
            from antcode_core.infrastructure.redis import get_redis_client

            worker = await worker_service.get_worker_by_id(execution.worker_id)
            if worker:
                redis = await get_redis_client()
                payload = build_cancel_control_payload(
                    run_id=execution.run_id,
                    reason=f"user_cancel:{current_user.user_id}",
                )
                # P2-24: 走 write_control_event 带 maxlen 近似裁剪,
                # 避免 control:{worker_id} stream 无限增长。
                await write_control_event(
                    redis, control_stream(worker.public_id), payload
                )
                cancelled = True
                logger.info(f"已发送取消指令到 Worker: {worker.name}")
            else:
                send_error = "worker 不存在"
        except Exception as e:
            send_error = str(e)
            logger.warning(f"发送取消指令失败: {e}")

    # L3: 取消指令必须真正发出去才落 CANCELLED。发送失败仍置终态会导致
    # 「UI 显示已取消 / worker 仍在跑」，之后 worker 回传 SUCCESS 还会（叠加 B5）
    # 让状态复活。此处保持 execution 处于原状态，让前端 503 后可重试。
    if execution.worker_id and not cancelled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"取消指令发送失败，请重试：{send_error or '未知错误'}",
        )

    # 更新数据库状态（仅在没有 worker 或已成功发送时执行）
    from antcode_core.application.services.scheduler.execution_status_service import (
        execution_status_service,
    )

    await execution_status_service.update_runtime_status(
        run_id=execution.run_id,
        status="cancelled",
        status_at=datetime.now(UTC),
        error_message=f"用户取消 (user_id={current_user.user_id})",
    )

    logger.info(f"执行已取消: {run_id}, 远程取消={cancelled}")

    return success_response(
        {
            "run_id": run_id,
            "status": "cancelled",
            "remote_cancelled": cancelled,
        },
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
async def list_run_artifacts(
    run_id: str, current_user: TokenData = Depends(get_current_user)
):
    """列出某次执行产出的所有 artifacts（不含下载 URI，敏感字段脱敏）。"""
    execution = await scheduler_service.get_execution_with_permission(
        run_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    artifacts = _parse_artifacts(execution.result_data)
    return success_response(
        {"items": [_sanitize_artifact_public(a) for a in artifacts]},
        message=Messages.QUERY_SUCCESS,
    )


def _match_artifact_by_name(
    artifacts: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
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
    execution = await scheduler_service.get_execution_with_permission(
        run_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")
    artifacts = _parse_artifacts(execution.result_data)
    artifact = _match_artifact_by_name(artifacts, artifact_name)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"产物不存在: {artifact_name}")

    content_hash = _extract_content_hash(artifact.get("uri", ""))
    if not content_hash:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="产物 URI 不合法或已下线"
        )

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
    execution = await scheduler_service.get_execution_with_permission(
        run_id, current_user.user_id
    )
    if not execution:
        raise HTTPException(status_code=404, detail="执行记录不存在或无权访问")

    # 从 Redis 直接读取 spider:data:{run_id} stream
    try:
        from antcode_core.infrastructure.redis import get_redis_client
        from antcode_core.infrastructure.redis.keys import RedisKeys
        from antcode_core.common.config import settings as _settings

        redis = await get_redis_client()
        if redis is None:
            return success_response(
                {"items": [], "last_id": start_id, "note": "Redis 不可用"},
                message=Messages.QUERY_SUCCESS,
            )
        keys = RedisKeys(namespace=_settings.REDIS_NAMESPACE)
        stream_key = keys.spider_data_stream(run_id)
        min_id = f"({start_id}" if start_id and start_id != "0" else "-"
        raw = await redis.xrange(stream_key, min=min_id, max="+", count=count)
    except Exception as exc:
        logger.warning(f"读取 spider items 失败: run_id={run_id} err={exc}")
        return success_response(
            {"items": [], "last_id": start_id},
            message=Messages.QUERY_SUCCESS,
        )

    items: list[dict[str, Any]] = []
    last_id: str = start_id
    for msg_id, fields in raw or []:
        decoded_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        last_id = decoded_id
        row: dict[str, Any] = {"_id": decoded_id}
        # 解 bytes
        for k, v in (fields or {}).items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            # data 字段是 JSON 字符串
            if key == "data" and isinstance(val, str):
                try:
                    row["data"] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    row["data"] = val
            else:
                row[key] = val
        items.append(row)

    return success_response(
        {"items": items, "last_id": last_id, "count": len(items)},
        message=Messages.QUERY_SUCCESS,
    )


router = runs_router

__all__ = ["runs_router", "router"]
