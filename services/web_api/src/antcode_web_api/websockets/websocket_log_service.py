"""
WebSocket日志服务 - 生产环境优化版本
负责处理WebSocket日志推送的业务逻辑
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import HTTPException
from loguru import logger
from tortoise.exceptions import DoesNotExist

from antcode_core.common.security.auth import verify_token
from antcode_core.domain.models.task_run import TaskRun
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.users.user_service import user_service
from antcode_web_api.websockets.websocket_connection_manager import websocket_manager


class WebSocketLogService:
    """WebSocket日志服务 - 生产环境优化版本"""

    async def connect(self, websocket, execution_id, token):
        """处理WebSocket连接"""
        connection_id: str | None = None

        try:
            # 1. 验证JWT令牌
            try:
                token_data = await verify_token(token)
                user_id = token_data.user_id
            except HTTPException as e:
                logger.warning(f"WebSocket认证失败: {e.detail}")
                await self._reject_connection(websocket, 4001, f"认证失败: {e.detail}")
                return

            # 2. 验证执行记录权限
            try:
                execution = await self._verify_execution_access(execution_id, user_id)
            except HTTPException as e:
                logger.warning(f"WebSocket权限验证失败: {e.detail}")
                await self._reject_connection(websocket, 4003, e.detail)
                return
            except DoesNotExist:
                logger.warning(f"执行记录不存在: {execution_id}")
                await self._reject_connection(websocket, 4004, "执行记录不存在")
                return

            # 3. 建立连接
            connection_id = await websocket_manager.connect(websocket, execution_id, user_id)
            logger.info(f"WebSocket连接成功: {connection_id}")

            # 4. 发送当前执行状态（让前端立即获取最新状态）
            await self._send_current_status(execution_id, execution)

            # 5. 推送历史日志并开启实时推送（Redis Streams）
            from antcode_web_api.websockets.redis_log_stream_service import (
                redis_log_stream_service,
            )

            await redis_log_stream_service.subscribe(execution_id)

            # 6. 处理客户端消息（阻塞直到断开）
            await self._handle_client_messages(websocket, execution_id, connection_id)

        except Exception:
            logger.exception(
                "WebSocket 连接处理异常: execution_id={}, connection_id={}",
                execution_id,
                connection_id,
            )
            if connection_id is None:
                await self._reject_connection(websocket, 4000, "服务器内部错误")
        finally:
            # 清理
            if connection_id:
                await websocket_manager.disconnect(websocket, execution_id)
                from antcode_web_api.websockets.redis_log_stream_service import (
                    redis_log_stream_service,
                )

                await redis_log_stream_service.unsubscribe(execution_id)

    async def _reject_connection(self, websocket, code, reason):
        """拒绝连接"""
        try:
            await websocket.accept()
            await websocket.send_json(
                {
                    "type": "error",
                    "code": code,
                    "message": reason,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            await websocket.close(code=code, reason=reason)
        except Exception as e:
            logger.debug(f"拒绝连接时异常: {e}")

    async def _verify_execution_access(self, execution_id, user_id):
        """验证用户对执行记录的访问权限"""
        # 获取执行记录（等待最多 5 秒，因为执行记录可能还在创建中）
        execution = None
        for _ in range(10):
            execution = await TaskRun.get_or_none(execution_id=execution_id)
            if execution:
                break
            await asyncio.sleep(0.5)

        if not execution:
            raise DoesNotExist(f"执行记录不存在: {execution_id}")

        # 检查用户权限
        user = await user_service.get_user_by_id(user_id)

        # 管理员可以访问所有执行记录
        if user and user.is_admin:
            return execution

        # 获取关联任务验证普通用户权限
        task = await relation_service.get_task_by_id(execution.task_id)

        if not task:
            # 任务已删除，只有管理员可以访问
            raise HTTPException(status_code=404, detail="关联任务不存在或已删除")

        if task.user_id == user_id:
            return execution
        else:
            raise HTTPException(status_code=403, detail="无权访问此执行记录")

    async def _send_current_status(self, execution_id, execution):
        """发送当前执行状态（让前端立即获取最新状态）"""
        try:
            status = execution.status.value if execution.status else "QUEUED"

            # 构建状态消息
            message = f"当前状态: {status}"
            if status == "RUNNING":
                message = "任务正在执行中"
            elif status == "SUCCESS":
                message = "任务执行成功"
            elif status == "FAILED":
                message = "任务执行失败"
            elif status == "QUEUED":
                message = "任务排队中"

            await websocket_manager.send_execution_status(
                execution_id=execution_id,
                status=status,
                progress=100.0 if status in ("SUCCESS", "FAILED", "TIMEOUT", "CANCELLED") else None,
                message=message,
            )

            logger.debug(f"已发送当前状态: {execution_id} -> {status}")

        except Exception as e:
            logger.warning(f"发送当前状态失败: {e}")

    async def _handle_client_messages(self, websocket, execution_id, connection_id):
        """处理客户端消息"""
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    await websocket_manager.handle_client_message(execution_id, connection_id, data)
                except Exception as e:
                    error_str = str(e)
                    # 正常关闭
                    if any(code in error_str for code in ["1000", "1001", "1005"]):
                        logger.debug(f"客户端正常断开: {connection_id}")
                    else:
                        logger.warning(f"客户端消息处理异常: {e}")
                    break
        except Exception as e:
            logger.error(f"消息循环异常: {e}")


# 全局实例
websocket_log_service = WebSocketLogService()
