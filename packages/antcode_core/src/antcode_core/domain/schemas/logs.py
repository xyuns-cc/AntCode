"""
日志相关的Pydantic模式定义
包含日志查询、响应等数据模式
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LogLevel(StrEnum):
    """日志级别枚举"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogType(StrEnum):
    """日志类型枚举"""

    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"
    APPLICATION = "application"


class LogFormat(StrEnum):
    """日志输出格式枚举"""

    STRUCTURED = "structured"  # 结构化JSON格式
    RAW = "raw"  # 原始文本格式


class LogEntry(BaseModel):
    """日志条目模型"""

    id: int = 0
    timestamp: datetime
    level: LogLevel
    log_type: LogType
    run_id: str = ""
    task_id: str = Field("", description="任务公开ID")
    message: str
    source: str = ""
    line_number: int = 0
    extra_data: dict[str, Any] = Field(default_factory=dict)


class LogQueryParams(BaseModel):
    """日志查询参数"""

    page: int = Field(1, ge=1, description="页码")
    size: int = Field(50, ge=1, le=1000, description="每页数量")
    level: LogLevel | None = Field(None, description="日志级别过滤")
    log_type: LogType | None = Field(None, description="日志类型过滤")
    run_id: str | None = Field(None, description="运行ID过滤")
    task_id: str | None = Field(None, description="任务公开ID过滤")
    start_time: datetime | None = Field(None, description="开始时间")
    end_time: datetime | None = Field(None, description="结束时间")
    search: str | None = Field(None, description="搜索关键词")


class LogListResponse(BaseModel):
    """日志列表响应"""

    total: int
    page: int
    size: int
    items: list[LogEntry]


class UnifiedLogResponse(BaseModel):
    """统一日志响应（支持多种格式）"""

    run_id: str
    format: LogFormat
    log_type: str = ""

    # 结构化格式字段
    structured_data: LogListResponse = Field(
        default_factory=lambda: LogListResponse(total=0, page=1, size=50, items=[])
    )

    # 原始格式字段
    raw_content: str = ""
    file_size: int = 0
    lines_count: int = 0
