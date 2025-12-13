"""
WebSocket日志服务 - 生产环境优化版本
负责处理WebSocket日志推送的业务逻辑
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional, Dict

from fastapi import HTTPException, WebSocket
from loguru import logger
from tortoise.exceptions import DoesNotExist

from src.core.security.auth import verify_token
from src.models.scheduler import TaskExecution
from src.services.logs.task_log_service import task_log_service
from src.services.websockets.websocket_connection_manager import websocket_manager


class WebSocketLogService:
    """WebSocket日志服务 - 生产环境优化版本"""

    def __init__(self):
        self._file_watchers: Dict[str, asyncio.Task] = {}
        self._watcher_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, execution_id: str, token: str):
        """处理WebSocket连接"""
        connection_id: Optional[str] = None

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

            # 5. 发送历史日志
            await self._send_historical_logs(execution_id)

            # 6. 启动实时日志监控
            await self._start_log_monitoring(execution_id, execution)

            # 6. 处理客户端消息（阻塞直到断开）
            await self._handle_client_messages(websocket, execution_id, connection_id)

        except Exception as e:
            logger.error(f"WebSocket连接处理异常: {e}", exc_info=True)
            if connection_id is None:
                await self._reject_connection(websocket, 4000, "服务器内部错误")
        finally:
            # 清理
            if connection_id:
                await websocket_manager.disconnect(websocket, execution_id)
                await self._stop_log_monitoring(execution_id)

    async def _reject_connection(self, websocket: WebSocket, code: int, reason: str):
        """拒绝连接"""
        try:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "code": code,
                "message": reason,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            await websocket.close(code=code, reason=reason)
        except Exception as e:
            logger.debug(f"拒绝连接时异常: {e}")

    async def _verify_execution_access(self, execution_id: str, user_id: int) -> TaskExecution:
        """验证用户对执行记录的访问权限"""
        import asyncio

        # 获取执行记录（等待最多 5 秒，因为执行记录可能还在创建中）
        execution = None
        for _ in range(10):
            execution = await TaskExecution.get_or_none(execution_id=execution_id)
            if execution:
                break
            await asyncio.sleep(0.5)

        if not execution:
            raise DoesNotExist(f"执行记录不存在: {execution_id}")

        # 检查用户权限
        from src.services.users.user_service import user_service
        user = await user_service.get_user_by_id(user_id)

        # 管理员可以访问所有执行记录
        if user and user.is_admin:
            return execution

        # 获取关联任务验证普通用户权限
        from src.services.projects.relation_service import relation_service
        task = await relation_service.get_task_by_id(execution.task_id)

        if not task:
            # 任务已删除，只有管理员可以访问
            raise HTTPException(status_code=404, detail="关联任务不存在或已删除")

        if task.user_id == user_id:
            return execution
        else:
            raise HTTPException(status_code=403, detail="无权访问此执行记录")

    async def _send_current_status(self, execution_id: str, execution: TaskExecution):
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
                message = f"任务执行失败"
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

    async def _send_historical_logs(self, execution_id: str):
        """发送历史日志（合并本地日志和分布式日志）"""
        try:
            from src.services.nodes.distributed_log_service import distributed_log_service

            # 1. 获取本地执行日志（任务分发阶段的日志）
            local_logs = await task_log_service.get_execution_logs(execution_id)
            local_output = local_logs.get("output", "").strip()
            local_error = local_logs.get("error", "").strip()

            # 2. 获取分布式日志（Worker 执行的日志）
            distributed_logs = await distributed_log_service.get_all_logs(execution_id)
            distributed_output = "\n".join(distributed_logs.get("stdout", []))
            distributed_error = "\n".join(distributed_logs.get("stderr", []))

            # 3. 合并日志（本地日志在前，分布式日志在后）
            all_output = "\n".join(filter(None, [local_output, distributed_output]))
            all_error = "\n".join(filter(None, [local_error, distributed_error]))

            has_output = bool(all_output)
            has_error = bool(all_error)

            if not has_output and not has_error:
                await websocket_manager.send_no_historical_logs(execution_id)
                return

            await websocket_manager.send_historical_logs_start(execution_id)

            sent_lines = 0

            # 发送标准输出
            if has_output:
                for line in all_output.split('\n'):
                    if line.strip():
                        await websocket_manager.send_log_message(
                            execution_id, "stdout", line.strip(), "INFO"
                        )
                        sent_lines += 1
                        if sent_lines % 100 == 0:
                            await asyncio.sleep(0.01)

            # 发送错误输出
            if has_error:
                for line in all_error.split('\n'):
                    if line.strip():
                        await websocket_manager.send_log_message(
                            execution_id, "stderr", line.strip(), "ERROR"
                        )
                        sent_lines += 1
                        if sent_lines % 100 == 0:
                            await asyncio.sleep(0.01)

            await websocket_manager.send_historical_logs_end(execution_id, sent_lines)
            logger.debug(f"发送历史日志完成: {execution_id}, {sent_lines} 行 (本地+分布式)")

        except Exception as e:
            logger.error(f"发送历史日志失败: {e}")
            await websocket_manager.send_no_historical_logs(execution_id)

    async def _start_log_monitoring(self, execution_id: str, execution: TaskExecution):
        """启动日志文件监控"""
        async with self._watcher_lock:
            if execution_id in self._file_watchers:
                return

            task = asyncio.create_task(
                self._monitor_log_files(execution_id, execution)
            )
            self._file_watchers[execution_id] = task
            logger.debug(f"启动日志监控: {execution_id}")

    async def _stop_log_monitoring(self, execution_id: str):
        """停止日志文件监控"""
        async with self._watcher_lock:
            if execution_id not in self._file_watchers:
                return

            task = self._file_watchers.pop(execution_id)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.debug(f"停止日志监控: {execution_id}")

    async def _monitor_log_files(self, execution_id: str, execution: TaskExecution):
        """监控日志文件变化（支持本地和分布式日志）"""
        stdout_pos = 0
        stderr_pos = 0
        distributed_stdout_pos = 0
        distributed_stderr_pos = 0

        # 获取分布式日志文件路径
        from src.services.nodes.distributed_log_service import distributed_log_service
        distributed_stdout_path = distributed_log_service._get_log_file(execution_id, "stdout")
        distributed_stderr_path = distributed_log_service._get_log_file(execution_id, "stderr")

        # 初始化位置为当前文件大小（跳过历史日志）
        if execution.log_file_path and os.path.exists(execution.log_file_path):
            stdout_pos = os.path.getsize(execution.log_file_path)
        if execution.error_log_path and os.path.exists(execution.error_log_path):
            stderr_pos = os.path.getsize(execution.error_log_path)

        # 分布式日志文件位置
        if os.path.exists(distributed_stdout_path):
            distributed_stdout_pos = os.path.getsize(distributed_stdout_path)
        if os.path.exists(distributed_stderr_path):
            distributed_stderr_pos = os.path.getsize(distributed_stderr_path)

        try:
            while websocket_manager.get_connections_for_execution(execution_id) > 0:
                # 检查本地标准输出
                if execution.log_file_path and os.path.exists(execution.log_file_path):
                    stdout_pos = await self._check_and_send_new_logs(
                        execution_id, execution.log_file_path, "stdout", stdout_pos
                    )

                # 检查本地错误输出
                if execution.error_log_path and os.path.exists(execution.error_log_path):
                    stderr_pos = await self._check_and_send_new_logs(
                        execution_id, execution.error_log_path, "stderr", stderr_pos
                    )

                # 检查分布式标准输出（Worker 上报的日志）
                if os.path.exists(distributed_stdout_path):
                    distributed_stdout_pos = await self._check_and_send_new_logs(
                        execution_id, distributed_stdout_path, "stdout", distributed_stdout_pos
                    )

                # 检查分布式错误输出
                if os.path.exists(distributed_stderr_path):
                    distributed_stderr_pos = await self._check_and_send_new_logs(
                        execution_id, distributed_stderr_path, "stderr", distributed_stderr_pos
                    )

                await asyncio.sleep(0.5)  # 500ms 检查间隔

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"日志监控异常: {execution_id}, {e}")

    async def _check_and_send_new_logs(
        self,
        execution_id: str,
        file_path: str,
        log_type: str,
        last_pos: int
    ) -> int:
        """检查并发送新日志"""
        try:
            current_size = os.path.getsize(file_path)

            if current_size <= last_pos:
                return last_pos

            # 读取新内容
            content = await self._read_file_chunk(file_path, last_pos, current_size)

            if content:
                level = "ERROR" if log_type == "stderr" else "INFO"
                for line in content.split('\n'):
                    if line.strip():
                        await websocket_manager.send_log_message(
                            execution_id, log_type, line.strip(), level
                        )

            return current_size

        except Exception as e:
            logger.error(f"检查日志文件失败: {e}")
            return last_pos

    async def _read_file_chunk(self, file_path: str, start: int, end: int) -> str:
        """读取文件片段"""
        def _read():
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(start)
                return f.read(end - start)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read)

    async def _handle_client_messages(
        self,
        websocket: WebSocket,
        execution_id: str,
        connection_id: str
    ):
        """处理客户端消息"""
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    await websocket_manager.handle_client_message(
                        execution_id, connection_id, data
                    )
                except Exception as e:
                    error_str = str(e)
                    # 正常关闭
                    if any(code in error_str for code in ['1000', '1001', '1005']):
                        logger.debug(f"客户端正常断开: {connection_id}")
                    else:
                        logger.warning(f"客户端消息处理异常: {e}")
                    break
        except Exception as e:
            logger.error(f"消息循环异常: {e}")


# 全局实例
websocket_log_service = WebSocketLogService()
