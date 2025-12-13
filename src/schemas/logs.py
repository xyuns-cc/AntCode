"""
日志相关的Pydantic模式定义
包含日志查询、响应等数据模式
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    """日志级别枚举"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogType(str, Enum):
    """日志类型枚举"""
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"
    APPLICATION = "application"


class LogFormat(str, Enum):
    """日志输出格式枚举"""
    STRUCTURED = "structured"  # 结构化JSON格式
    RAW = "raw"               # 原始文本格式


class LogEntry(BaseModel):
    """日志条目模型"""
    id: Optional[int] = None
    timestamp: datetime
    level: LogLevel
    log_type: LogType
    execution_id: Optional[str] = None
    task_id: Optional[str] = Field(None, description="任务公开ID")
    message: str
    source: Optional[str] = None
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    extra_data: Optional[Dict[str, Any]] = None


class LogQueryParams(BaseModel):
    """日志查询参数"""
    page: int = Field(1, ge=1, description="页码")
    size: int = Field(50, ge=1, le=1000, description="每页数量")
    level: Optional[LogLevel] = Field(None, description="日志级别过滤")
    log_type: Optional[LogType] = Field(None, description="日志类型过滤")
    execution_id: Optional[str] = Field(None, description="执行ID过滤")
    task_id: Optional[str] = Field(None, description="任务公开ID过滤")
    start_time: Optional[datetime] = Field(None, description="开始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    search: Optional[str] = Field(None, description="搜索关键词")


class LogListResponse(BaseModel):
    """日志列表响应"""
    total: int
    page: int
    size: int
    items: List[LogEntry]


class LogFileInfo(BaseModel):
    """日志文件信息"""
    file_path: str
    file_size: int
    lines_count: int
    created_time: Optional[datetime] = None
    modified_time: Optional[datetime] = None


class LogFileResponse(BaseModel):
    """日志文件响应"""
    execution_id: str
    log_type: str
    content: str
    file_path: str
    file_size: int
    lines_count: int
    last_modified: Optional[datetime] = None


class UnifiedLogResponse(BaseModel):
    """统一日志响应（支持多种格式）"""
    execution_id: str
    format: LogFormat
    log_type: Optional[str] = None

    # 结构化格式字段
    structured_data: Optional[LogListResponse] = None

    # 原始格式字段  
    raw_content: Optional[str] = None
    file_path: Optional[str] = None
    file_size: Optional[int] = None
    lines_count: Optional[int] = None
    last_modified: Optional[datetime] = None


class LogStreamMessage(BaseModel):
    """日志流消息"""
    type: str = Field(..., description="消息类型")
    execution_id: Optional[str] = Field(None, description="执行ID")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")
    data: Dict[str, Any] = Field(..., description="消息数据")


class LogStreamStats(BaseModel):
    """日志流统计信息"""
    total_connections: int
    active_executions: int
    messages_sent: int
    uptime_seconds: float


class LogArchiveRequest(BaseModel):
    """日志归档请求"""
    execution_ids: Optional[List[str]] = Field(None, description="指定执行ID列表")
    start_date: Optional[datetime] = Field(None, description="开始日期")
    end_date: Optional[datetime] = Field(None, description="结束日期")
    compress: bool = Field(True, description="是否压缩")


class LogArchiveResponse(BaseModel):
    """日志归档响应"""
    archive_id: str
    file_path: str
    file_size: int
    execution_count: int
    created_at: datetime


class LogCleanupRequest(BaseModel):
    """日志清理请求"""
    older_than_days: int = Field(30, ge=1, le=365, description="清理多少天前的日志")
    execution_ids: Optional[List[str]] = Field(None, description="指定执行ID列表")
    dry_run: bool = Field(False, description="是否为试运行")


class LogCleanupResponse(BaseModel):
    """日志清理响应"""
    deleted_files: int
    freed_space_bytes: int
    deleted_executions: List[str]
    dry_run: bool


class LogMetrics(BaseModel):
    """日志指标"""
    total_log_files: int
    total_size_bytes: int
    oldest_log_date: Optional[datetime] = None
    newest_log_date: Optional[datetime] = None
    log_levels_count: Dict[str, int]
    log_types_count: Dict[str, int]
    daily_log_count: Dict[str, int]  # 日期 -> 日志数量


class RealTimeLogMessage(BaseModel):
    """实时日志消息"""
    type: str = "log_line"
    execution_id: str
    log_type: LogType
    content: str
    timestamp: datetime
    level: LogLevel = LogLevel.INFO



