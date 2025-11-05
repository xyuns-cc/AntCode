# src/models/scheduler.py
"""任务调度相关数据模型"""
from tortoise import fields
from tortoise.models import Model

from .enums import TaskStatus, TaskType, ScheduleType


class ScheduledTask(Model):
    """调度任务表"""
    id = fields.BigIntField(pk=True)
    name = fields.CharField(max_length=255, unique=True, description="任务名称")
    description = fields.TextField(null=True, description="任务描述")

    # 关联项目 - 使用应用层外键
    project_id = fields.BigIntField(description="关联项目ID")
    task_type = fields.CharEnumField(TaskType, description="任务类型")
    
    # 调度配置
    schedule_type = fields.CharEnumField(ScheduleType, description="调度类型")
    cron_expression = fields.CharField(max_length=100, null=True, description="Cron表达式")
    interval_seconds = fields.IntField(null=True, description="间隔秒数")
    scheduled_time = fields.DatetimeField(null=True, description="计划执行时间")
    
    # 执行配置
    max_instances = fields.IntField(default=1, description="最大并发实例数")
    timeout_seconds = fields.IntField(default=3600, description="超时时间(秒)")
    retry_count = fields.IntField(default=3, description="重试次数")
    retry_delay = fields.IntField(default=60, description="重试延迟(秒)")
    
    # 状态信息
    status = fields.CharEnumField(TaskStatus, default=TaskStatus.PENDING)
    is_active = fields.BooleanField(default=True, description="是否激活")
    last_run_time = fields.DatetimeField(null=True, description="最后运行时间")
    next_run_time = fields.DatetimeField(null=True, description="下次运行时间")
    failure_count = fields.IntField(default=0, description="失败次数")
    success_count = fields.IntField(default=0, description="成功次数")
    
    # 执行参数
    execution_params = fields.JSONField(null=True, description="执行参数")
    environment_vars = fields.JSONField(null=True, description="环境变量")
    
    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    user_id = fields.BigIntField(description="创建者ID")
    
    class Meta:
        table = "scheduled_tasks"
        indexes = [("name",), ("status",), ("is_active",)]

    def __str__(self):
        return f"{self.name} ({self.status})"


class TaskExecution(Model):
    """任务执行记录表"""
    id = fields.BigIntField(pk=True)
    task_id = fields.BigIntField(description="关联任务ID")
    
    # 执行信息
    execution_id = fields.CharField(max_length=64, unique=True, description="执行ID")
    start_time = fields.DatetimeField(description="开始时间")
    end_time = fields.DatetimeField(null=True, description="结束时间")
    duration_seconds = fields.FloatField(null=True, description="执行时长(秒)")
    
    # 状态和结果
    status = fields.CharEnumField(TaskStatus, description="执行状态")
    exit_code = fields.IntField(null=True, description="退出码")
    error_message = fields.TextField(null=True, description="错误信息")
    retry_count = fields.IntField(default=0, description="重试次数")
    
    # 日志文件路径
    log_file_path = fields.CharField(max_length=512, null=True, description="日志文件路径")
    error_log_path = fields.CharField(max_length=512, null=True, description="错误日志文件路径")
    result_data = fields.JSONField(null=True, description="结果数据")
    
    # 统计信息
    cpu_usage = fields.FloatField(null=True, description="CPU使用率")
    memory_usage = fields.BigIntField(null=True, description="内存使用(bytes)")
    
    created_at = fields.DatetimeField(auto_now_add=True)
    
    class Meta:
        table = "task_executions"
        indexes = [("execution_id",), ("status",), ("start_time",)]
        ordering = ["-start_time"]



