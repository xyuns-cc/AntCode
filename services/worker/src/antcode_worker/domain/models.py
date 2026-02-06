"""
Worker 域模型定义

定义 Worker 执行侧所需的最小模型集合。
注意：这些模型不等同于 antcode_core 的 MySQL 模型。

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from antcode_worker.domain.enums import (
    ArtifactType,
    ExitReason,
    LogStream,
    RunStatus,
    TaskType,
)


@dataclass
class RunContext:
    """
    执行上下文

    包含一次执行所需的所有上下文信息。

    Requirements: 3.1
    """

    run_id: str                          # 执行实例 ID（全局唯一）
    task_id: str                         # 任务 ID
    project_id: str                      # 项目 ID

    # 运行时规格
    runtime_spec: Optional["RuntimeSpec"] = None

    # 资源限制
    timeout_seconds: int = 3600          # 执行超时（秒）
    memory_limit_mb: int = 0             # 内存限制（MB，0=不限制）
    cpu_limit_seconds: int = 0           # CPU 时间限制（秒，0=不限制）

    # 元数据
    priority: int = 0                    # 优先级（越大越高）
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = field(default_factory=datetime.now)

    # 传输层信息
    receipt: str | None = None        # 任务回执（用于 ack/requeue）


@dataclass
class RuntimeSpec:
    """
    运行时规格

    定义执行环境的确定性字段，用于计算 runtime_hash。

    Requirements: 6.2
    """

    # Python 规格
    python_version: str | None = None      # Python 版本（如 "3.11"）
    python_path: str | None = None         # 指定 Python 路径

    # 依赖锁定
    lock_source: str | None = None         # uv.lock 内容哈希或 URI
    requirements: list[str] = field(default_factory=list)  # requirements.txt 内容

    # 可选约束
    constraints: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)

    # 非确定性字段（不影响 runtime_hash）
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskPayload:
    """
    任务数据

    包含任务的输入数据和参数。

    Requirements: 3.2
    """

    task_type: TaskType = TaskType.CODE

    # 项目信息
    project_path: str | None = None        # 本地项目路径
    download_url: str | None = None        # 项目下载 URL
    file_hash: str | None = None           # 文件哈希（用于缓存）
    is_compressed: bool | None = None      # 是否为压缩包（None 表示自动检测）

    # 执行入口
    entry_point: str = ""                     # 入口文件
    function: str | None = None            # 入口函数

    # 参数
    args: list[str] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)

    # 环境变量（运行时注入）
    env_vars: dict[str, str] = field(default_factory=dict)

    # 输入数据
    inputs: dict[str, Any] = field(default_factory=dict)

    # 产物配置
    artifact_patterns: list[str] = field(default_factory=list)  # 产物匹配模式


@dataclass
class ExecPlan:
    """
    执行计划

    由 Plugin 生成，描述如何执行任务。
    Plugin 只产出 ExecPlan，不直接执行。

    Requirements: 3.3
    """

    # 命令（必填字段放在前面）
    command: str                              # 可执行文件路径

    # 运行 ID（可选）
    run_id: str | None = None

    # 命令参数
    args: list[str] = field(default_factory=list)

    # 环境
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None                 # 工作目录

    # 超时
    timeout_seconds: int = 3600
    grace_period_seconds: int = 10            # SIGTERM 后等待时间

    # 资源限制
    memory_limit_mb: int = 0
    cpu_limit_seconds: int = 0

    # 产物策略
    artifact_patterns: list[str] = field(default_factory=list)
    collect_stdout: bool = True
    collect_stderr: bool = True

    # 沙箱配置
    sandbox_enabled: bool = False
    sandbox_config: dict[str, Any] = field(default_factory=dict)

    # 元数据
    plugin_name: str | None = None         # 生成此计划的插件名


@dataclass
class ExecResult:
    """
    执行结果

    Requirements: 3.4
    """

    run_id: str
    status: RunStatus

    # 退出信息
    exit_code: int | None = None
    exit_reason: ExitReason = ExitReason.NORMAL
    error_message: str | None = None

    # 时间信息
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float = 0

    # 资源使用
    cpu_time_seconds: float = 0
    memory_peak_mb: float = 0

    # 产物
    artifacts: list["ArtifactRef"] = field(default_factory=list)

    # 日志统计
    stdout_lines: int = 0
    stderr_lines: int = 0
    log_archived: bool = False
    log_archive_uri: str | None = None

    # 额外数据
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "exit_reason": self.exit_reason.value,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "cpu_time_seconds": self.cpu_time_seconds,
            "memory_peak_mb": self.memory_peak_mb,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "stdout_lines": self.stdout_lines,
            "stderr_lines": self.stderr_lines,
            "log_archived": self.log_archived,
            "log_archive_uri": self.log_archive_uri,
            "data": self.data,
        }


@dataclass
class LogEntry:
    """
    日志记录

    Requirements: 3.5
    """

    run_id: str
    stream: LogStream
    content: str

    # 序列号（用于排序和去重）
    seq: int = 0

    # 时间戳
    timestamp: datetime | None = field(default_factory=datetime.now)

    # 元数据
    level: str = "INFO"                       # 日志级别
    source: str | None = None              # 来源（如文件名）

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "run_id": self.run_id,
            "stream": self.stream.value,
            "content": self.content,
            "seq": self.seq,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "source": self.source,
        }


@dataclass
class ArtifactRef:
    """
    产物引用

    Requirements: 3.6
    """

    name: str                                 # 产物名称
    artifact_type: ArtifactType = ArtifactType.FILE

    # 存储位置
    uri: str | None = None                 # 存储 URI
    local_path: str | None = None          # 本地路径

    # 元数据
    size_bytes: int = 0
    checksum: str | None = None            # SHA256
    mime_type: str | None = None

    # 时间
    created_at: datetime | None = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "type": self.artifact_type.value,
            "uri": self.uri,
            "local_path": self.local_path,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "mime_type": self.mime_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class RuntimeHandle:
    """
    运行时句柄

    由 RuntimeManager 返回，表示一个准备好的运行时环境。

    Requirements: 6.1
    """

    path: str                                 # 虚拟环境路径
    runtime_hash: str                         # 运行时哈希
    python_executable: str                    # Python 可执行文件路径

    # 元数据
    python_version: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
