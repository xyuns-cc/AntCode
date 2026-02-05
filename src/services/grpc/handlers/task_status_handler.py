"""
任务状态处理器

处理节点上报的任务状态更新，更新数据库并触发后续钩子。
**Validates: Requirements 4.3, 4.4**
"""
from datetime import datetime
from typing import Any, Optional

from loguru import logger

from src.services.grpc.dispatcher import MessageHandler, NodeContext
from src.grpc_generated import TaskStatus


class TaskStatusHandler(MessageHandler):
    """任务状态处理器
    
    处理节点发送的任务状态更新：
    1. 更新数据库中的执行记录
    """

    async def handle(self, message: TaskStatus, context: NodeContext) -> Optional[Any]:
        """处理任务状态消息
        
        Args:
            message: 任务状态消息
            context: 节点上下文
            
        Returns:
            None
        """
        try:
            execution_id = message.execution_id
            status = message.status
            exit_code = message.exit_code if message.HasField("exit_code") else None
            error_message = message.error_message if message.HasField("error_message") else None
            
            logger.info(
                f"收到任务状态更新 - 节点: {context.node_id}, "
                f"执行ID: {execution_id}, 状态: {status}"
            )
            
            # 更新数据库
            await self._update_execution_in_database(
                execution_id=execution_id,
                status=status,
                exit_code=exit_code,
                error_message=error_message,
            )

            return None
            
        except Exception as e:
            logger.error(f"任务状态处理异常 - 节点: {context.node_id}, 错误: {e}")
            raise
    
    async def _update_execution_in_database(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int],
        error_message: Optional[str],
    ) -> None:
        """更新数据库中的执行记录
        
        Args:
            execution_id: 执行 ID
            status: 状态
            exit_code: 退出码
            error_message: 错误消息
        """
        try:
            from src.services.nodes.distributed_log_service import distributed_log_service
            
            # 使用分布式日志服务更新状态
            # 该服务会自动处理数据库更新和前端推送
            await distributed_log_service.update_task_status(
                execution_id=execution_id,
                status=status,
                exit_code=exit_code,
                error_message=error_message,
            )
            
        except ImportError:
            logger.warning("distributed_log_service 不可用，尝试直接更新数据库")
            await self._direct_update_database(
                execution_id=execution_id,
                status=status,
                exit_code=exit_code,
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"更新执行记录失败: {e}")
    
    async def _direct_update_database(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int],
        error_message: Optional[str],
    ) -> None:
        """直接更新数据库（备用方法）"""
        try:
            from src.models.scheduler import TaskExecution
            from src.models.enums import TaskStatus as TaskStatusEnum
            from datetime import timezone
            
            execution = await TaskExecution.get_or_none(execution_id=execution_id)
            if not execution:
                logger.warning(f"执行记录不存在: {execution_id}")
                return
            
            # 状态映射
            status_map = {
                "running": TaskStatusEnum.RUNNING,
                "success": TaskStatusEnum.SUCCESS,
                "failed": TaskStatusEnum.FAILED,
                "timeout": TaskStatusEnum.TIMEOUT,
                "cancelled": TaskStatusEnum.CANCELLED,
            }
            
            new_status = status_map.get(status.lower())
            if not new_status:
                logger.warning(f"未知状态: {status}")
                return
            
            execution.status = new_status
            if exit_code is not None:
                execution.exit_code = exit_code
            if error_message:
                execution.error_message = error_message
            
            # 如果是终态，设置结束时间
            if status.lower() in {"success", "failed", "timeout", "cancelled"}:
                now = datetime.now(timezone.utc)
                execution.end_time = now
                if execution.start_time:
                    start_time = execution.start_time
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)
                    execution.duration_seconds = (now - start_time).total_seconds()
            
            await execution.save()
            logger.info(f"执行记录已更新: {execution_id} -> {new_status}")
            
        except Exception as e:
            logger.error(f"直接更新数据库失败: {e}")


# 全局处理器实例
task_status_handler = TaskStatusHandler()
