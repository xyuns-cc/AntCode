"""
Worker 域枚举定义

Requirements: 3.7
"""

from enum import Enum


class RunStatus(str, Enum):
    """运行状态"""

    PENDING = "pending"          # 等待执行
    PREPARING = "preparing"      # 准备运行时环境
    RUNNING = "running"          # 执行中
    SUCCESS = "success"          # 执行成功
    FAILED = "failed"            # 执行失败
    CANCELLED = "cancelled"      # 已取消
    TIMEOUT = "timeout"          # 执行超时
    KILLED = "killed"            # 被强制终止


class LogStream(str, Enum):
    """日志流类型"""

    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"            # Worker 系统日志


class TaskType(str, Enum):
    """任务类型"""

    CODE = "code"                # 代码执行
    SPIDER = "spider"            # 爬虫任务
    RENDER = "render"            # 渲染任务
    CUSTOM = "custom"            # 自定义任务


class ExitReason(str, Enum):
    """退出原因"""

    NORMAL = "normal"            # 正常退出
    ERROR = "error"              # 执行错误
    TIMEOUT = "timeout"          # 超时
    CANCELLED = "cancelled"      # 用户取消
    KILLED = "killed"            # 被强制终止
    OOM = "oom"                  # 内存超限
    CPU_LIMIT = "cpu_limit"      # CPU 时间超限
    SIGNAL = "signal"            # 收到信号
    RUNTIME_ERROR = "runtime_error"  # 运行时环境错误


class ArtifactType(str, Enum):
    """产物类型"""

    FILE = "file"                # 普通文件
    LOG = "log"                  # 日志文件
    REPORT = "report"            # 报告
    DATA = "data"                # 数据文件
    ARCHIVE = "archive"          # 压缩包
