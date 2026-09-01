"""Worker 执行侧的最小模型集合——与 antcode_core 的 PostgreSQL 模型不是一回事。"""

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


@dataclass(frozen=True)
class SourceBundle:
    """任务源码包引用。"""

    uri: str
    sha256: str
    size: int
    transfer_method: str = "source_bundle"
    entry_point: str = ""
    resolved_revision: str = ""
    source_subdir: str = ""


@dataclass
class RunContext:
    """一次执行所需的全部上下文。"""

    run_id: str  # 全局唯一
    task_id: str
    project_id: str

    runtime_spec: Optional["RuntimeSpec"] = None

    timeout_seconds: int = 3600
    memory_limit_mb: int = 0  # 0=不限制
    cpu_limit_seconds: int = 0  # 秒，0=不限制

    priority: int = 0  # 越大越高
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime | None = field(default_factory=datetime.now)

    receipt: str | None = None  # 任务回执，用于 ack/requeue


@dataclass
class RuntimeSpec:
    """执行环境规格；确定性字段用于算 runtime_hash。"""

    python_version: str | None = None  # 如 "3.11"
    python_path: str | None = None

    lock_source: str | None = None  # uv.lock 内容哈希或 URI
    requirements: list[str] = field(default_factory=list)  # requirements.txt 内容

    constraints: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)

    # 非确定性，不影响 runtime_hash
    env_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class TaskPayload:
    """任务的输入数据与参数。"""

    task_type: TaskType = TaskType.CODE

    # 执行工作区，由 source bundle 解包生成
    workspace_path: str = ""
    project_cwd: str = ""
    source_bundle: SourceBundle | None = None

    run_id: str = ""
    project_id: str = ""

    entry_point: str = ""
    function: str | None = None

    args: list[str] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)

    env_vars: dict[str, str] = field(default_factory=dict)

    inputs: dict[str, Any] = field(default_factory=dict)
    artifact_patterns: list[str] = field(default_factory=list)


@dataclass
class ExecPlan:
    """由 Plugin 生成、描述如何执行任务；Plugin 只产出计划，不直接执行。"""

    command: str  # 可执行文件的绝对路径

    run_id: str | None = None

    args: list[str] = field(default_factory=list)

    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    # source bundle 解包根目录（cwd 的上级）。沙箱按它暴露 include_paths 共享目录。
    workspace_root: str | None = None

    timeout_seconds: int = 3600
    grace_period_seconds: int = 10  # SIGTERM 后等待时间

    memory_limit_mb: int = 0
    cpu_limit_seconds: int = 0
    # 以下 POSIX rlimit 项，0 = 回落到 ExecutorConfig 的对应默认值
    max_open_files: int = 0
    max_processes: int = 0
    max_file_size_mb: int = 0
    enforce_rlimit: bool = True

    artifact_patterns: list[str] = field(default_factory=list)
    collect_stdout: bool = True
    collect_stderr: bool = True

    sandbox_enabled: bool = False
    sandbox_config: dict[str, Any] = field(default_factory=dict)

    plugin_name: str | None = None  # 生成此计划的插件名


@dataclass
class ExecResult:
    """
    执行结果

    进程内值对象：executor 产出，engine._task_result 逐字段翻成 TaskResult 后
    才出网。本类型自身没有序列化形态——Direct/Gateway 两条上报链路都走 proto
    (``data_pb2.TaskStatus``)，任何 to_dict/JSON 编解码都不会有消费者。

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

    # 产物
    artifacts: list["ArtifactRef"] = field(default_factory=list)

    # 日志统计
    stdout_lines: int = 0
    stderr_lines: int = 0

    # 额外数据
    data: dict[str, Any] = field(default_factory=dict)


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
    level: str = "INFO"  # 日志级别
    source: str | None = None  # 来源（如文件名）

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

    name: str  # 产物名称
    artifact_type: ArtifactType = ArtifactType.FILE

    # 存储位置
    uri: str | None = None  # 存储 URI
    local_path: str | None = None  # 本地路径

    # 元数据
    size_bytes: int = 0
    checksum: str | None = None  # SHA256
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

    path: str  # 虚拟环境路径
    runtime_hash: str  # 运行时哈希
    python_executable: str  # Python 可执行文件路径

    # 元数据
    python_version: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
