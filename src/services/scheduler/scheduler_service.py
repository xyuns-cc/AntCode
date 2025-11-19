# src/services/scheduler_service.py (更新版本)
"""任务调度服务"""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.core.config import settings
from src.models.enums import TaskStatus, ScheduleType, ProjectType
from src.models.scheduler import ScheduledTask, TaskExecution
from src.services.logs.task_log_service import task_log_service  # 新增
from src.services.projects.relation_service import relation_service  # 新增
from src.services.monitoring import monitoring_service
from src.services.scheduler.spider_dispatcher import spider_task_dispatcher
from src.services.scheduler.task_executor import TaskExecutor


class SchedulerService:
    """调度器服务"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            timezone=settings.SCHEDULER_TIMEZONE,
            job_defaults={
                'coalesce': True,  # 合并错过的执行
                'max_instances': 3,  # 每个任务的最大并发实例数
                'misfire_grace_time': 30  # 错过执行的宽限时间（秒）
            }
        )
        self.executor = TaskExecutor()
        self.running_tasks = {}
        
        # 并发控制 - 限制同时执行的任务数量
        self.concurrency_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)
        self.task_execution_stats = {
            "total_executed": 0,
            "currently_running": 0,
            "failed_count": 0,
            "success_count": 0
        }

    async def start(self):
        """启动调度器"""
        try:
            self.scheduler.start()
            logger.info("✅ 任务调度器已启动")

            # 加载已存在的活跃任务
            await self._load_active_tasks()
            
            # 添加定期清理工作目录的任务
            await self._add_cleanup_job()

            # 注册监控相关的周期任务
            await self._add_monitoring_jobs()

        except Exception as e:
            logger.error(f"启动调度器失败: {e}")
            raise

    async def create_task(self, task_data, project_type, user_id):
        """创建调度任务"""
        try:
            # 创建任务
            task = await ScheduledTask.create(
                **task_data.model_dump(exclude={'project_id'}),
                project_id=task_data.project_id,
                task_type=project_type,
                user_id=user_id
            )

            # 添加到调度器
            if task.is_active:
                await self.add_task(task)

            logger.info(f"任务创建成功: {task.name} (ID: {task.id})")
            return task
            
        except Exception as e:
            logger.error(f"创建任务失败: {e}")
            raise

    async def get_user_tasks(
        self,
        user_id,
        status = None,
        is_active = None,
        page = 1,
        size = 20
    ):
        """获取用户任务列表（包含创建者信息）"""
        try:
            from src.services.users.user_service import user_service
            
            # 如果user_id为None，表示管理员查看所有任务
            if user_id is None:
                query = ScheduledTask.all()
            else:
                query = ScheduledTask.filter(user_id=user_id)
            
            if status is not None:
                query = query.filter(status=status)
            if is_active is not None:
                query = query.filter(is_active=is_active)
            
            total = await query.count()
            offset = (page - 1) * size
            tasks = await query.offset(offset).limit(size).order_by('-created_at')
            
            # 获取创建者用户名信息
            user_ids = list(set([t.user_id for t in tasks]))
            users_map = {}
            if user_ids:
                users = await user_service.get_users_by_ids(user_ids)
                users_map = {user.id: user.username for user in users}
            
            # 为任务添加创建者用户名
            for task in tasks:
                task.created_by = task.user_id
                task.created_by_username = users_map.get(task.user_id)
            
            return {
                "tasks": tasks,
                "total": total,
                "page": page,
                "size": size,
                "pages": (total + size - 1) // size
            }
        except Exception as e:
            logger.error(f"获取用户任务列表失败: {e}")
            raise

    async def get_task_by_id(self, task_id, user_id):
        """根据ID获取任务"""
        from src.services.users.user_service import user_service
        
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以查看所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能查看自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return None
                
            # 获取创建者用户名
            creator = await user_service.get_user_by_id(task.user_id)
            task.created_by = task.user_id
            task.created_by_username = creator.username if creator else None
            
            return task
        except Exception as e:
            logger.error(f"获取任务失败: {e}")
            raise

    async def update_task(self, task_id, task_data, user_id):
        """更新任务"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以更新所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能更新自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return None
                
            # 更新字段
            update_data = task_data.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(task, field, value)
            
            await task.save()
            
            # 如果任务状态改变，更新调度器
            if 'is_active' in update_data:
                if task.is_active:
                    await self.add_task(task)
                else:
                    await self.remove_task(task_id)
            
            logger.info(f"任务更新成功: {task.name} (ID: {task.id})")
            return task
            
        except Exception as e:
            logger.error(f"更新任务失败: {e}")
            raise

    async def delete_task(self, task_id, user_id):
        """删除任务"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以删除所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能删除自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return False
            
            # 从调度器移除（作业不存在时不报错）
            await self.remove_task(task_id)
            
            # 删除数据库记录
            await task.delete()
            
            logger.info(f"任务删除成功: {task.name} (ID: {task.id})")
            return True
            
        except Exception as e:
            logger.error(f"删除任务失败: {e}")
            raise

    async def get_task_executions(
        self,
        task_id,
        user_id,
        status = None,
        start_date = None,
        end_date = None,
        page = 1,
        size = 20
    ):
        """获取任务执行记录"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以查看所有任务的执行记录
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能查看自己任务的执行记录
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                raise ValueError("任务不存在或无权访问")
            
            query = TaskExecution.filter(task_id=task_id)
            
            if status is not None:
                query = query.filter(status=status)
            if start_date:
                query = query.filter(start_time__gte=start_date)
            if end_date:
                query = query.filter(start_time__lte=end_date)
            
            total = await query.count()
            offset = (page - 1) * size
            executions = await query.offset(offset).limit(size).order_by('-start_time')
            
            return {
                "executions": executions,
                "total": total,
                "page": page,
                "size": size,
                "pages": (total + size - 1) // size
            }
        except Exception as e:
            logger.error(f"获取任务执行记录失败: {e}")
            raise

    async def get_execution_by_id(self, execution_id):
        """根据ID获取执行记录"""
        try:
            return await TaskExecution.get_or_none(execution_id=execution_id)
        except Exception as e:
            logger.error(f"获取执行记录失败: {e}")
            raise

    async def get_task_stats(self, task_id, user_id):
        """获取任务统计信息"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以查看所有任务的统计信息
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能查看自己任务的统计信息
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return None
                
            # 获取执行统计
            executions = await TaskExecution.filter(task_id=task_id).all()
            
            total = len(executions)
            if total == 0:
                return {
                    "task_id": task_id,
                    "total_executions": 0,
                    "success_count": 0,
                    "failed_count": 0,
                    "running_count": 0,
                    "success_rate": 0.0,
                    "avg_duration": 0.0,
                    "last_execution": None
                }
            
            success_count = sum(1 for e in executions if e.status == TaskStatus.SUCCESS)
            failed_count = sum(1 for e in executions if e.status == TaskStatus.FAILED) 
            running_count = sum(1 for e in executions if e.status == TaskStatus.RUNNING)
            
            # 计算平均执行时长
            completed_executions = [e for e in executions if e.end_time and e.start_time]
            avg_duration = 0.0
            if completed_executions:
                durations = [(e.end_time - e.start_time).total_seconds() for e in completed_executions]
                avg_duration = sum(durations) / len(durations)
            
            # 获取最后执行
            last_execution = max(executions, key=lambda e: e.start_time) if executions else None
            
            return {
                "task_id": task_id,
                "total_executions": total,
                "success_count": success_count,
                "failed_count": failed_count,
                "running_count": running_count,
                "success_rate": success_count / total * 100,
                "avg_duration": avg_duration,
                "last_execution": {
                    "execution_id": last_execution.execution_id,
                    "status": last_execution.status,
                    "start_time": last_execution.start_time,
                    "end_time": last_execution.end_time
                } if last_execution else None
            }
        except Exception as e:
            logger.error(f"获取任务统计失败: {e}")
            raise

    async def verify_admin_permission(self, user_id):
        """验证管理员权限"""
        try:
            from src.models.user import User
            user = await User.get_or_none(id=user_id)
            return user and user.is_admin
        except Exception as e:
            logger.error(f"验证管理员权限失败: {e}")
            return False

    async def get_user_task_ids(self, user_id):
        """获取用户所有任务ID列表"""
        try:
            tasks = await ScheduledTask.filter(user_id=user_id).all()
            return [task.id for task in tasks]
        except Exception as e:
            logger.error(f"获取用户任务ID失败: {e}")
            return []

    async def get_task_executions_by_task_ids(self, task_ids):
        """根据任务ID列表获取所有执行记录"""
        try:
            if not task_ids:
                return []
            return await TaskExecution.filter(task_id__in=task_ids).all()
        except Exception as e:
            logger.error(f"获取任务执行记录失败: {e}")
            return []

    async def pause_task_by_user(self, task_id, user_id):
        """暂停用户任务"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以暂停所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能暂停自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return False
            
            try:
                await self.pause_task(task_id)
            except ValueError:
                return False
            return True
        except Exception as e:
            logger.error(f"暂停任务失败: {e}")
            raise

    async def resume_task_by_user(self, task_id, user_id):
        """恢复用户任务"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以恢复所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能恢复自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return False
            
            try:
                await self.resume_task(task_id)
            except ValueError:
                return False
            return True
        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            raise

    async def trigger_task_by_user(self, task_id, user_id):
        """立即触发用户任务"""
        try:
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以触发所有任务
                task = await ScheduledTask.get_or_none(id=task_id)
            else:
                # 普通用户只能触发自己的任务
                task = await ScheduledTask.get_or_none(id=task_id, user_id=user_id)
                
            if not task:
                return False
            
            await self.trigger_task(task_id)
            return True
        except Exception as e:
            logger.error(f"触发任务失败: {e}")
            raise

    async def get_execution_with_permission(self, execution_id, user_id):
        """获取执行记录（带权限验证）"""
        try:
            execution = await TaskExecution.get_or_none(execution_id=execution_id)
            if not execution:
                return None
                
            # 检查用户是否为管理员
            is_admin = await self.verify_admin_permission(user_id)
            
            if is_admin:
                # 管理员可以查看所有执行记录
                return execution
            else:
                # 普通用户只能查看自己任务的执行记录
                task = await ScheduledTask.get_or_none(id=execution.task_id, user_id=user_id)
                if not task:
                    return None
                    
                return execution
        except Exception as e:
            logger.error(f"获取执行记录失败: {e}")
            raise

    async def shutdown(self):
        """关闭调度器"""
        try:
            self.scheduler.shutdown(wait=True)
            logger.info("任务调度器已关闭")
        except Exception as e:
            logger.error(f"关闭调度器失败: {e}")

    async def _load_active_tasks(self):
        """加载活跃任务"""
        try:
            active_tasks = await ScheduledTask.filter(is_active=True).all()
            for task in active_tasks:
                await self.add_task(task)
                logger.info(f"加载任务: {task.name}")
        except Exception as e:
            logger.error(f"加载活跃任务失败: {e}")

    async def add_task(self, task):
        """添加任务到调度器"""
        try:
            # 创建触发器
            trigger = self._create_trigger(task)

            # 添加作业
            self.scheduler.add_job(
                func=self._execute_task,
                trigger=trigger,
                id=str(task.id),
                name=task.name,
                kwargs={'task_id': task.id},
                replace_existing=True
            )

            logger.info(f"✅ 任务 {task.name} 已添加到调度器")

        except Exception as e:
            logger.error(f"添加任务失败: {e}")
            raise

    async def remove_task(self, task_id):
        """从调度器移除任务"""
        try:
            self.scheduler.remove_job(str(task_id))
            logger.info(f"任务 {task_id} 已从调度器移除")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，视为已移除")
        except Exception as e:
            logger.error(f"移除任务失败: {e}")
            raise

    async def pause_task(self, task_id):
        """暂停任务"""
        try:
            self.scheduler.pause_job(str(task_id))

            # 更新数据库状态
            task = await ScheduledTask.get(id=task_id)
            task.status = TaskStatus.PAUSED
            task.is_active = False
            await task.save()

            logger.info(f"任务 {task_id} 已暂停")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法暂停")
            raise ValueError("任务不存在或已执行完成，无法暂停")
        except Exception as e:
            logger.error(f"暂停任务失败: {e}")
            raise

    async def resume_task(self, task_id):
        """恢复任务"""
        try:
            self.scheduler.resume_job(str(task_id))

            # 更新数据库状态
            task = await ScheduledTask.get(id=task_id)
            task.status = TaskStatus.PENDING
            task.is_active = True
            await task.save()

            logger.info(f"任务 {task_id} 已恢复")
        except JobLookupError:
            logger.warning(f"任务 {task_id} 在调度器中不存在，可能已执行完成或未激活，无法恢复")
            raise ValueError("任务不存在或已执行完成，无法恢复")
        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            raise

    async def trigger_task(self, task_id):
        """立即触发任务"""
        try:
            # 检查任务是否存在于调度器中
            job = self.scheduler.get_job(str(task_id))
            if job:
                # 如果存在，修改下次运行时间为现在
                try:
                    aware_now = datetime.now(self.scheduler.timezone)
                except Exception:
                    aware_now = datetime.now(timezone.utc)
                job.modify(next_run_time=aware_now)
                logger.info(f"任务 {task_id} 已触发")
            else:
                # 如果不存在，创建一个临时作业来执行
                logger.info(f"任务 {task_id} 不在调度器中，创建临时作业执行")
                
                # 使用唯一的job_id，包含时间戳避免冲突
                temp_job_id = f"{task_id}_manual_{datetime.now().timestamp()}"
                
                # 添加一个立即执行的作业
                self.scheduler.add_job(
                    func=self._execute_task,
                    trigger=DateTrigger(
                        run_date=(datetime.now(self.scheduler.timezone) if hasattr(self.scheduler, 'timezone') and self.scheduler.timezone else datetime.now(timezone.utc))
                    ),
                    id=temp_job_id,
                    kwargs={'task_id': task_id},
                    replace_existing=True
                )
        except Exception as e:
            logger.error(f"触发任务失败: {e}")
            raise

    def _create_trigger(self, task):
        """创建触发器"""
        if task.schedule_type == ScheduleType.CRON:
            return CronTrigger.from_crontab(task.cron_expression)
        elif task.schedule_type == ScheduleType.INTERVAL:
            return IntervalTrigger(seconds=task.interval_seconds)
        elif task.schedule_type == ScheduleType.DATE:
            return DateTrigger(run_date=task.scheduled_time)
        elif task.schedule_type == ScheduleType.ONCE:
            return DateTrigger(run_date=task.scheduled_time or datetime.now())
        else:
            raise ValueError(f"不支持的调度类型: {task.schedule_type}")

    async def _execute_task(self, task_id):
        """执行任务的核心方法（带并发控制）"""
        # 使用信号量控制并发数
        async with self.concurrency_semaphore:
            await self._execute_task_internal(task_id)
    
    async def _execute_task_internal(self, task_id):
        """执行任务的内部实现"""
        execution_id = str(uuid.uuid4())
        task = None
        execution = None

        # 更新统计信息
        self.task_execution_stats["total_executed"] += 1
        self.task_execution_stats["currently_running"] += 1

        try:
            # 获取任务及其关联信息
            task_info = await relation_service.get_task_with_project(task_id)
            if not task_info:
                logger.error(f"任务 {task_id} 不存在")
                return

            task = task_info["task"]
            project = task_info["project"]
            project_detail = task_info["project_detail"]

            # 检查任务是否可以执行
            if not task.is_active:
                logger.warning(f"任务 {task.name} 未激活，跳过执行")
                return
                
            # 记录并发状态
            current_running = self.task_execution_stats["currently_running"]
            max_concurrent = settings.MAX_CONCURRENT_TASKS
            logger.info(f"🚀 开始执行任务 {task.name} (当前并发: {current_running}/{max_concurrent})")

            # 生成日志文件路径
            log_paths = task_log_service.generate_log_paths(execution_id, task.name)

            # 创建执行记录
            now = datetime.now(timezone.utc)
            execution = await TaskExecution.create(
                execution_id=execution_id,
                task_id=task.id,  # 应用层外键
                status=TaskStatus.RUNNING,
                start_time=now,
                log_file_path=log_paths["log_file_path"],
                error_log_path=log_paths["error_log_path"],
                retry_count=0
            )

            # 确保执行记录已保存到数据库
            await execution.save()

            # 更新任务状态
            task.status = TaskStatus.RUNNING
            task.last_run_time = now
            await task.save()

            # 记录到运行中任务
            self.running_tasks[execution_id] = {
                'task_id': task_id,
                'task_name': task.name,
                'start_time': now
            }

            # 推送开始状态到WebSocket
            await self._push_execution_status(execution, {
                "status": "RUNNING",
                "message": "任务开始执行",
                "task_name": task.name,
                "start_time": now.isoformat()
            })

            # 记录日志
            await self._log_execution(
                execution,
                "INFO",
                f"开始执行任务: {task.name}"
            )

            # 根据项目类型执行不同的逻辑
            if project.type == ProjectType.RULE:
                # 规则项目：提交到Redis
                result = await self._execute_rule_task(task, project, project_detail, execution)
            else:
                # 文件/代码项目：本地执行
                result = await self.executor.execute(
                    project=project,
                    execution_id=execution_id,
                    params=task.execution_params,
                    environment_vars=task.environment_vars,
                    timeout=task.timeout_seconds or settings.TASK_EXECUTION_TIMEOUT
                )

            # 处理执行结果
            if result.get('success'):
                execution.status = TaskStatus.SUCCESS
                execution.result = result
                task.status = TaskStatus.SUCCESS
                task.success_count += 1

                # 保存日志文件路径
                if result.get('log_file_path'):
                    execution.log_file_path = result['log_file_path']
                if result.get('error_log_path'):
                    execution.error_log_path = result['error_log_path']

                await self._log_execution(
                    execution,
                    "INFO",
                    f"任务执行成功: {result.get('message', '执行完成')}"
                )

                # 推送成功状态到WebSocket
                await self._push_execution_status(execution, {
                    "status": "SUCCESS",
                    "message": "任务执行成功",
                    "result": result
                })
            else:
                execution.status = TaskStatus.FAILED
                execution.error_message = result.get('error')
                task.status = TaskStatus.FAILED
                task.failure_count += 1

                # 保存日志文件路径
                if result.get('log_file_path'):
                    execution.log_file_path = result['log_file_path']
                if result.get('error_log_path'):
                    execution.error_log_path = result['error_log_path']

                await self._log_execution(
                    execution,
                    "ERROR",
                    f"任务执行失败: {result.get('error')}"
                )

                # 推送失败状态到WebSocket
                await self._push_execution_status(execution, {
                    "status": "FAILED",
                    "message": "任务执行失败",
                    "error": result.get('error')
                })

                # 检查是否需要重试
                if task.retry_count > 0 and execution.retry_count < task.retry_count:
                    await self._schedule_retry(task, execution)

        except asyncio.TimeoutError:
            if execution:
                execution.status = TaskStatus.TIMEOUT
                execution.error_message = "任务执行超时"
            if task:
                task.status = TaskStatus.TIMEOUT
                task.failure_count += 1

            await self._log_execution(
                execution,
                "ERROR",
                "任务执行超时"
            )

        except Exception as e:
            logger.error(f"执行任务失败: {e}")
            if execution:
                execution.status = TaskStatus.FAILED
                execution.error_message = str(e)
            if task:
                task.status = TaskStatus.FAILED
                task.failure_count += 1

            await self._log_execution(
                execution,
                "ERROR",
                f"任务执行异常: {str(e)}"
            )

        finally:
            # 更新并发统计
            self.task_execution_stats["currently_running"] -= 1
            
            # 更新成功/失败统计
            if execution and execution.status == TaskStatus.SUCCESS:
                self.task_execution_stats["success_count"] += 1
            elif execution and execution.status == TaskStatus.FAILED:
                self.task_execution_stats["failed_count"] += 1
            
            # 清理运行中任务
            if execution_id in self.running_tasks:
                del self.running_tasks[execution_id]

            # 更新执行记录
            if execution:
                execution.end_time = datetime.now(timezone.utc)
                if execution.start_time:
                    execution.duration_seconds = (
                            execution.end_time - execution.start_time
                    ).total_seconds()
                await execution.save()

            # 更新任务状态
            if task:
                if task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.PENDING
                task.next_run_time = self._get_next_run_time(task_id)
                await task.save()
                
            # 记录任务完成状态
            current_running = self.task_execution_stats["currently_running"]
            max_concurrent = settings.MAX_CONCURRENT_TASKS
            logger.info(f"✅ 任务执行完成 (当前并发: {current_running}/{max_concurrent})")

    async def _execute_rule_task(
            self,
            task,
            project,
            rule_detail,
            execution
    ):
        """执行规则任务 - 根据配置选择执行器"""
        try:
            if not rule_detail:
                return {
                    "success": False,
                    "error": "规则项目详情不存在"
                }

            # 准备参数
            params = task.execution_params or {}
            params["scheduled_task_id"] = task.id
            params["scheduled_task_name"] = task.name

            # 根据规则配置决定提交策略
            if rule_detail.pagination_config and \
                    rule_detail.pagination_config.get("method") == "url_pattern":
                # URL分页模式：可能需要提交多个任务
                tasks_submitted = []
                start_page = rule_detail.pagination_config.get("start_page", 1)
                max_pages = rule_detail.pagination_config.get("max_pages", 10)

                for page in range(start_page, start_page + max_pages):
                    page_params = params.copy()
                    page_params["page_number"] = page

                    # 替换URL中的页码
                    original_url = rule_detail.target_url
                    if "{}" in original_url:
                        rule_detail.target_url = original_url.format(page)

                    result = await spider_task_dispatcher.submit_rule_task(
                        project=project,
                        rule_detail=rule_detail,
                        execution_id=f"{execution.execution_id}_page_{page}",
                        params=page_params
                    )

                    rule_detail.target_url = original_url  # 恢复原URL

                    if result["success"]:
                        tasks_submitted.append(result["task_id"])

                    await self._log_execution(
                        execution,
                        "INFO",
                        f"提交页面 {page} 到{result.get('queue', 'local:scrapy')}: {result.get('task_id')}"
                    )

                return {
                    "success": True,
                    "message": f"成功提交 {len(tasks_submitted)} 个任务到执行器",
                    "task_ids": tasks_submitted,
                    "total_pages": len(tasks_submitted)
                }
            else:
                # 单任务提交
                result = await spider_task_dispatcher.submit_rule_task(
                    project=project,
                    rule_detail=rule_detail,
                    execution_id=execution.execution_id,
                    params=params
                )

                if result["success"]:
                    await self._log_execution(
                        execution,
                        "INFO",
                        f"任务已提交到{result.get('queue', 'local:scrapy')}: {result.get('task_id')}"
                    )

                    return {
                        "success": True,
                        "message": f"任务已提交到{result.get('queue', '本地执行器')}",
                        "task_id": result.get("task_id"),
                        "queue": result.get("queue")
                    }
                else:
                    return {
                        "success": False,
                        "error": result.get("message", "提交失败")
                    }

        except Exception as e:
            logger.error(f"执行规则任务失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _schedule_retry(self, task, execution):
        """调度重试"""
        execution.retry_count += 1
        await execution.save()

        # 延迟后重试
        retry_delay = task.retry_delay or settings.TASK_RETRY_DELAY
        
        # 使用唯一的job_id，包含execution_id以避免冲突
        job_id = f"{task.id}_retry_{execution.execution_id}_{execution.retry_count}"
        
        # 先尝试移除可能存在的旧作业
        try:
            self.scheduler.remove_job(job_id)
        except:
            pass  # 忽略作业不存在的错误

        try:
            base_time = datetime.now(self.scheduler.timezone) if hasattr(self.scheduler, 'timezone') and self.scheduler.timezone else datetime.now(timezone.utc)
        except Exception:
            base_time = datetime.now(timezone.utc)

        self.scheduler.add_job(
            func=self._execute_task,
            trigger=DateTrigger(
                run_date=base_time + timedelta(seconds=retry_delay)
            ),
            id=job_id,
            kwargs={'task_id': task.id},
            replace_existing=True  # 如果存在则替换
        )

        await self._log_execution(
            execution,
            "INFO",
            f"任务将在 {retry_delay} 秒后重试 (第{execution.retry_count}次)"
        )

    async def _log_execution(
            self,
            execution,
            level,
            message
    ):
        """记录执行日志"""
        if execution and execution.log_file_path:
            # 写入日志文件并实时推送到WebSocket
            log_content = f"[{level}] {message}"
            if level.upper() in ["ERROR", "CRITICAL"]:
                # 错误日志写入错误日志文件
                if execution.error_log_path:
                    await task_log_service.write_log(
                        execution.error_log_path,
                        log_content,
                        execution_id=execution.execution_id
                    )
            else:
                # 普通日志写入输出日志文件
                await task_log_service.write_log(
                    execution.log_file_path,
                    log_content,
                    execution_id=execution.execution_id
                )

    async def _push_execution_status(self, execution, status_data):
        """推送执行状态到WebSocket客户端（已移除WebSocket功能）"""
        # WebSocket功能已被移除，此方法保留以保持兼容性
        pass

    def _get_next_run_time(self, task_id):
        """获取下次运行时间"""
        job = self.scheduler.get_job(str(task_id))
        if job and job.next_run_time:
            return job.next_run_time
        return None

    def get_running_tasks(self):
        """获取运行中的任务"""
        return list(self.running_tasks.values())
    
    def get_execution_stats(self):
        """获取任务执行统计信息"""
        return {
            **self.task_execution_stats,
            "success_rate": (
                self.task_execution_stats["success_count"] / 
                max(1, self.task_execution_stats["total_executed"])
            ) * 100,
            "max_concurrent_tasks": settings.MAX_CONCURRENT_TASKS,
            "available_slots": settings.MAX_CONCURRENT_TASKS - self.task_execution_stats["currently_running"]
        }

    async def _add_cleanup_job(self):
        """添加定期清理工作目录的任务"""
        try:
            # 每天凌晨2点执行清理
            self.scheduler.add_job(
                func=self._cleanup_workspaces,
                trigger=CronTrigger(hour=2, minute=0),
                id="workspace_cleanup",
                name="清理执行工作目录",
                replace_existing=True
            )
            logger.info("✅ 已添加定期清理工作目录任务（每天凌晨2点执行）")
        except Exception as e:
            logger.error(f"添加清理任务失败: {e}")

    async def _cleanup_workspaces(self):
        """执行工作目录清理"""
        try:
            logger.info("🧹 开始清理过期的执行工作目录...")
            await self.executor.cleanup_old_workspaces(
                max_age_hours=settings.CLEANUP_WORKSPACE_MAX_AGE_HOURS
            )
            logger.info("✅ 工作目录清理完成")
        except Exception as e:
            logger.error(f"清理工作目录失败: {e}")

    async def _add_monitoring_jobs(self):
        """注册监控数据处理任务"""
        if not settings.MONITORING_ENABLED:
            logger.info("监控功能未启用，跳过监控任务注册")
            return

        try:
            self.scheduler.add_job(
                func=self._process_monitoring_stream,
                trigger=IntervalTrigger(seconds=settings.MONITOR_STREAM_INTERVAL),
                id="monitoring_process_stream",
                name="监控数据流处理",
                replace_existing=True,
            )

            self.scheduler.add_job(
                func=self._cleanup_monitoring_data,
                trigger=CronTrigger(hour=3, minute=30),
                id="monitoring_cleanup_data",
                name="监控历史数据清理",
                replace_existing=True,
            )
            logger.info("✅ 已注册监控数据处理任务")
        except Exception as e:
            logger.error(f"注册监控任务失败: {e}")

    async def _process_monitoring_stream(self):
        """处理监控数据流"""
        try:
            processed = await monitoring_service.process_stream()
            if processed:
                logger.debug("处理监控流数据 %s 条", processed)
        except Exception as e:
            logger.error(f"处理监控数据流失败: {e}")

    async def _cleanup_monitoring_data(self):
        """清理过期的监控历史数据"""
        try:
            await monitoring_service.cleanup_old_data()
            logger.info("监控历史数据清理完成")
        except Exception as e:
            logger.error(f"清理监控历史数据失败: {e}")


# 创建全局调度器服务实例
scheduler_service = SchedulerService()
