"""
任务服务

负责处理任务分发、状态上报和取消。

Requirements: 11.3
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable, Dict, Any

from loguru import logger

from ..domain.models import TaskStatus, TaskDispatch, TaskCancel
from ..domain.interfaces import TaskService as ITaskService
from ..domain.events import TaskReceived, TaskStatusChanged, TaskCancelled, event_bus
from ..transport.protocol import TransportProtocol


class TaskServiceImpl(ITaskService):
    """
    任务服务实现
    
    特性:
    - 任务状态上报
    - 任务分发处理
    - 任务取消处理
    - 事件发布
    
    Requirements: 11.3
    """

    def __init__(self, transport: TransportProtocol):
        """
        初始化任务服务
        
        Args:
            transport: 传输协议实例
        """
        self._transport = transport
        
        # 回调函数
        self._on_task_received: Optional[Callable[[TaskDispatch], Awaitable[None]]] = None
        self._on_task_cancelled: Optional[Callable[[TaskCancel], Awaitable[None]]] = None
        
        # 注册传输层回调
        self._transport.on_task_dispatch(self._handle_task_dispatch)
        self._transport.on_task_cancel(self._handle_task_cancel)
        
        # 任务状态缓存（用于事件发布）
        self._task_status_cache: Dict[str, str] = {}

    async def report_status(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """上报任务状态"""
        try:
            task_status = TaskStatus(
                execution_id=execution_id,
                status=status,
                exit_code=exit_code,
                error_message=error_message,
                timestamp=datetime.now(),
            )

            success = await self._transport.send_task_status(task_status)

            if success:
                # 获取旧状态
                old_status = self._task_status_cache.get(execution_id, "unknown")
                self._task_status_cache[execution_id] = status

                # 发布状态变更事件
                await event_bus.publish(TaskStatusChanged(
                    execution_id=execution_id,
                    old_status=old_status,
                    new_status=status,
                    exit_code=exit_code,
                    error_message=error_message,
                ))

                logger.debug(f"任务状态上报成功: {execution_id} -> {status}")
                
                # 清理已完成任务的缓存
                if status in ("success", "failed", "cancelled", "timeout"):
                    self._task_status_cache.pop(execution_id, None)

            return success

        except Exception as e:
            logger.error(f"任务状态上报异常: {e}")
            return False

    def on_task_received(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """注册任务接收回调"""
        self._on_task_received = callback

    def on_task_cancelled(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """注册任务取消回调"""
        self._on_task_cancelled = callback

    async def _handle_task_dispatch(self, task: TaskDispatch):
        """处理任务分发"""
        try:
            logger.info(f"收到任务分发: task_id={task.task_id}, project_id={task.project_id}")

            # 发布任务接收事件
            await event_bus.publish(TaskReceived(
                task=task,
                accepted=True,
            ))

            # 调用用户回调
            if self._on_task_received:
                await self._on_task_received(task)

            # 发送确认
            await self._transport.send_task_ack(task.task_id, accepted=True)

        except Exception as e:
            logger.error(f"处理任务分发异常: {e}")
            
            # 发布拒绝事件
            await event_bus.publish(TaskReceived(
                task=task,
                accepted=False,
                reject_reason=str(e),
            ))

            # 发送拒绝
            await self._transport.send_task_ack(task.task_id, accepted=False, reason=str(e))

    async def _handle_task_cancel(self, cancel: TaskCancel):
        """处理任务取消"""
        try:
            logger.info(f"收到任务取消: task_id={cancel.task_id}, execution_id={cancel.execution_id}")

            success = False
            reason = None

            # 调用用户回调
            if self._on_task_cancelled:
                try:
                    await self._on_task_cancelled(cancel)
                    success = True
                except Exception as e:
                    reason = str(e)
                    logger.warning(f"任务取消回调异常: {e}")

            # 发布取消事件
            await event_bus.publish(TaskCancelled(
                cancel_request=cancel,
                success=success,
                reason=reason,
            ))

            # 发送确认
            await self._transport.send_cancel_ack(cancel.task_id, success=success, reason=reason)

        except Exception as e:
            logger.error(f"处理任务取消异常: {e}")
            await self._transport.send_cancel_ack(cancel.task_id, success=False, reason=str(e))

    def get_task_status(self, execution_id: str) -> Optional[str]:
        """获取任务状态（从缓存）"""
        return self._task_status_cache.get(execution_id)

    def get_active_tasks(self) -> Dict[str, str]:
        """获取所有活跃任务状态"""
        return dict(self._task_status_cache)
