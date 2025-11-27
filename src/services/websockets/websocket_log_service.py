"""WebSocket日志服务"""
import asyncio
import os
from datetime import datetime, timezone

from fastapi import HTTPException
from loguru import logger
from tortoise.exceptions import DoesNotExist

from src.core.auth import verify_token
from src.models.scheduler import TaskExecution
from src.services.logs.task_log_service import task_log_service
from src.services.projects.relation_service import relation_service
from src.services.users.user_service import user_service
from src.services.websockets.websocket_connection_manager import websocket_manager


class WebSocketLogService:
    """WebSocket日志服务"""
    
    def __init__(self):
        self.file_watchers = {}
    
    async def connect(self, websocket, execution_id, token):
        """处理WebSocket连接"""
        try:
            # 验证JWT令牌
            token_data = await verify_token(token)
            user_id = token_data.user_id
            
            # 验证执行记录权限
            execution = await self._verify_execution_access(execution_id, user_id)
            
            # 建立连接
            connection_id = await websocket_manager.connect(websocket, execution_id, user_id)
            
            # 发送历史日志
            await self._send_historical_logs(execution_id)
            
            # 启动实时日志监控
            await self._start_log_monitoring(execution_id, execution)
            
            # 处理客户端消息
            await self._handle_client_messages(websocket, execution_id)
            
        except HTTPException as e:
            logger.error(f"WebSocket连接认证失败: {e.detail}")
            await websocket.close(code=4003, reason=e.detail)
        except DoesNotExist:
            logger.error(f"执行记录不存在: {execution_id}")
            await websocket.close(code=4004, reason="执行记录不存在")
        except Exception as e:
            logger.error(f"WebSocket连接处理失败: {e}")
            await websocket.close(code=4000, reason="服务器内部错误")
        finally:
            # 清理连接
            await websocket_manager.disconnect(websocket, execution_id)
            # 停止日志监控
            await self._stop_log_monitoring(execution_id)
    
    async def _verify_execution_access(self, execution_id, user_id):
        """验证用户对执行记录的访问权限"""
        try:
            execution = await TaskExecution.get(execution_id=execution_id)
            
            # 通过关联服务验证权限
            task = await relation_service.get_task_by_id(execution.task_id)
            
            if not task:
                raise HTTPException(status_code=404, detail="任务不存在")
            
            # 检查用户是否为管理员
            user = await user_service.get_user_by_id(user_id)
            
            # 管理员可以访问所有执行记录，普通用户只能访问自己的
            if user and user.is_admin:
                logger.info(f"管理员用户 {user.username} 访问执行记录 {execution_id}")
                return execution
            elif task.user_id == user_id:
                logger.info(f"用户访问自己的执行记录 {execution_id}")
                return execution
            else:
                logger.warning(f"用户 {user_id} 无权访问执行记录 {execution_id}，任务创建者: {task.user_id}")
                raise HTTPException(status_code=403, detail="无权访问此执行记录")
            
        except DoesNotExist:
            raise
        except Exception as e:
            logger.error(f"验证执行记录权限失败: {e}")
            raise HTTPException(status_code=500, detail="权限验证失败")
    
    async def _send_historical_logs(self, execution_id):
        """发送历史日志"""
        try:
            # 获取历史日志
            logs_data = await task_log_service.get_execution_logs(execution_id)
            
            if not logs_data.get("output") and not logs_data.get("error"):
                await websocket_manager.send_no_historical_logs(execution_id)
                return
            
            await websocket_manager.send_historical_logs_start(execution_id)
            
            sent_lines = 0
            
            # 发送标准输出日志
            if logs_data.get("output"):
                stdout_lines = logs_data["output"].strip().split('\n')
                for line in stdout_lines:
                    if line.strip():
                        await websocket_manager.send_log_message(
                            execution_id, "stdout", line.strip(), "INFO"
                        )
                        sent_lines += 1
                        # 控制发送速度，避免消息过多
                        if sent_lines % 50 == 0:
                            await asyncio.sleep(0.01)
            
            # 发送错误输出日志
            if logs_data.get("error"):
                stderr_lines = logs_data["error"].strip().split('\n')
                for line in stderr_lines:
                    if line.strip():
                        await websocket_manager.send_log_message(
                            execution_id, "stderr", line.strip(), "ERROR"
                        )
                        sent_lines += 1
                        # 控制发送速度
                        if sent_lines % 50 == 0:
                            await asyncio.sleep(0.01)
            
            await websocket_manager.send_historical_logs_end(execution_id, sent_lines)
            logger.info(f"📤 发送历史日志完成: {execution_id}, 共 {sent_lines} 行")
            
        except Exception as e:
            logger.error(f"发送历史日志失败: {e}")
            await websocket_manager.send_no_historical_logs(execution_id)
    
    async def _start_log_monitoring(self, execution_id, execution):
        """启动日志文件监控"""
        if execution_id in self.file_watchers:
            return
        
        try:
            # 创建文件监控任务
            monitor_task = asyncio.create_task(
                self._monitor_log_files(execution_id, execution)
            )
            self.file_watchers[execution_id] = monitor_task
            
            logger.info(f"启动日志监控: {execution_id}")
            
        except Exception as e:
            logger.error(f"启动日志监控失败: {e}")
    
    async def _stop_log_monitoring(self, execution_id):
        """停止日志文件监控"""
        if execution_id not in self.file_watchers:
            return
        
        try:
            task = self.file_watchers[execution_id]
            task.cancel()
            del self.file_watchers[execution_id]
            
            logger.info(f"⏹️ 停止日志监控: {execution_id}")
            
        except Exception as e:
            logger.error(f"停止日志监控失败: {e}")
    
    async def _monitor_log_files(self, execution_id, execution):
        """监控日志文件变化"""
        last_stdout_size = 0
        last_stderr_size = 0
        last_stdout_pos = 0
        last_stderr_pos = 0
        
        try:
            while websocket_manager.get_connections_for_execution(execution_id) > 0:
                # 监控标准输出日志
                if execution.log_file_path and os.path.exists(execution.log_file_path):
                    last_stdout_pos = await self._check_log_file_changes(
                        execution_id, execution.log_file_path, "stdout", last_stdout_pos
                    )
                
                # 监控错误输出日志
                if execution.error_log_path and os.path.exists(execution.error_log_path):
                    last_stderr_pos = await self._check_log_file_changes(
                        execution_id, execution.error_log_path, "stderr", last_stderr_pos
                    )
                
                # 每秒检查一次
                await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            logger.info(f"日志监控任务已取消: {execution_id}")
        except Exception as e:
            logger.error(f"日志监控异常: {e}")
    
    async def _check_log_file_changes(self, execution_id, file_path, log_type, last_pos):
        """检查日志文件变化并发送新内容"""
        try:
            current_size = os.path.getsize(file_path)
            
            if current_size > last_pos:
                # 读取新增内容
                new_content = await self._read_file_from_position(file_path, last_pos)
                
                if new_content:
                    # 按行发送新内容
                    lines = new_content.split('\n')
                    for line in lines:
                        if line.strip():  # 跳过空行
                            level = "ERROR" if log_type == "stderr" else "INFO"
                            await websocket_manager.send_log_message(
                                execution_id, log_type, line.strip(), level
                            )
                
                return current_size
            
            return last_pos
            
        except Exception as e:
            logger.error(f"检查日志文件变化失败: {e}")
            return last_pos
    
    async def _read_file_from_position(self, file_path, position):
        """从指定位置读取文件内容"""
        try:
            def read_sync():
                with open(file_path, 'r', encoding='utf-8') as f:
                    f.seek(position)
                    return f.read()
            
            return await asyncio.get_event_loop().run_in_executor(None, read_sync)
            
        except Exception as e:
            logger.error(f"读取文件失败: {e}")
            return ""
    
    async def _handle_client_messages(self, websocket, execution_id):
        """处理客户端发送的消息"""
        try:
            while True:
                try:
                    # 接收客户端消息
                    message = await websocket.receive_json()
                    await self._process_client_message(execution_id, message)
                    
                except Exception as e:
                    # 状态码 1000 是正常关闭，不记录为错误
                    error_str = str(e)
                    if '1000' in error_str or 'Component unmount' in error_str:
                        logger.debug(f"客户端正常断开连接: {e}")
                    else:
                        logger.error(f"处理客户端消息失败: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"客户端消息循环异常: {e}")
    
    async def _process_client_message(self, execution_id, message):
        """处理具体的客户端消息"""
        message_type = message.get("type")
        
        if message_type == "ping":
            # 心跳检测
            await websocket_manager.broadcast_to_execution(execution_id, {
                "type": "pong",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
        elif message_type == "get_stats":
            # 获取连接统计
            stats = websocket_manager.get_stats()
            await websocket_manager.broadcast_to_execution(execution_id, {
                "type": "stats",
                "data": stats,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
        else:
            logger.warning(f"未知的客户端消息类型: {message_type}")


# 创建全局服务实例
websocket_log_service = WebSocketLogService()