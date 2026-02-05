"""任务模型 - 统一的任务定义

设计原则:
1. 状态与 Master 端保持一致（success/failed/cancelled/timeout 等）
2. 支持多种任务类型（代码执行、爬虫、数据处理）
3. 灵活的参数配置
4. 丰富的执行状态
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskType(str, Enum):
    """任务类型"""
    CODE_EXECUTION = "code_execution"    # 代码执行
    SPIDER_CRAWL = "spider_crawl"        # 爬虫任务
    DATA_PROCESS = "data_process"        # 数据处理
    FILE_PROCESS = "file_process"        # 文件处理
    API_REQUEST = "api_request"          # API 请求
    BATCH_TASK = "batch_task"            # 批量任务


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"        # 等待中
    QUEUED = "queued"          # 已入队
    RUNNING = "running"        # 执行中
    SUCCESS = "success"        # 执行成功
    FAILED = "failed"          # 失败
    CANCELLED = "cancelled"    # 已取消
    TIMEOUT = "timeout"        # 超时
    PAUSED = "paused"          # 暂停


class TaskPriority(int, Enum):
    """任务优先级"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    IDLE = 4


class ProjectType(str, Enum):
    """项目类型"""
    FILE = "file"      # 文件项目
    CODE = "code"      # 代码项目
    RULE = "rule"      # 规则项目


# 默认优先级映射：根据项目类型分配默认优先级
DEFAULT_PRIORITY_MAP: Dict[ProjectType, TaskPriority] = {
    ProjectType.RULE: TaskPriority.HIGH,    # 规则项目默认高优先级
    ProjectType.CODE: TaskPriority.NORMAL,  # 代码项目默认普通优先级
    ProjectType.FILE: TaskPriority.NORMAL,  # 文件项目默认普通优先级
}


def get_default_priority(project_type: ProjectType) -> TaskPriority:
    """根据项目类型获取默认优先级"""
    return DEFAULT_PRIORITY_MAP.get(project_type, TaskPriority.NORMAL)


@dataclass
class TaskConfig:
    """任务配置"""
    timeout: int = 3600                    # 超时时间（秒）
    max_retries: int = 3                   # 最大重试次数
    retry_delay: float = 1.0               # 重试延迟（秒）
    cpu_limit: Optional[int] = None        # CPU 时间限制（秒）
    memory_limit: Optional[int] = None     # 内存限制（MB）
    priority: TaskPriority = TaskPriority.NORMAL
    delay: Optional[float] = None          # 延迟执行（秒）
    cron: Optional[str] = None             # 定时任务表达式
    depends_on: List[str] = field(default_factory=list)
    notify_on_success: bool = False
    notify_on_failure: bool = True
    webhook_url: Optional[str] = None


@dataclass
class CodeExecutionParams:
    """代码执行参数"""
    project_id: str                        # 项目ID
    entry_point: Optional[str] = None      # 入口文件
    args: List[str] = field(default_factory=list)
    env_vars: Dict[str, str] = field(default_factory=dict)
    working_dir: Optional[str] = None
    python_version: Optional[str] = None
    requirements: List[str] = field(default_factory=list)


@dataclass
class PageRules:
    """页面解析规则"""
    xpath_rules: Dict[str, str] = field(default_factory=dict)
    css_rules: Dict[str, str] = field(default_factory=dict)
    regex_rules: Dict[str, str] = field(default_factory=dict)


@dataclass
class SpiderCrawlParams:
    """爬虫任务参数
    
    支持两种模式:
    1. 单页模式: 直接解析 start_urls
    2. 列表+详情模式: 先从列表页提取详情链接，再解析详情页
    """
    spider_type: str = "custom"  # custom, list_detail, pagination
    start_urls: List[str] = field(default_factory=list)

    # 单页解析规则
    xpath_rules: Dict[str, str] = field(default_factory=dict)
    css_rules: Dict[str, str] = field(default_factory=dict)
    regex_rules: Dict[str, str] = field(default_factory=dict)

    # 列表页配置
    list_page: Optional[PageRules] = None
    list_link_xpath: Optional[str] = None  # 提取详情链接的 XPath
    list_link_css: Optional[str] = None    # 提取详情链接的 CSS

    # 详情页配置
    detail_page: Optional[PageRules] = None

    # 分页配置
    pagination_xpath: Optional[str] = None  # 下一页链接 XPath
    pagination_css: Optional[str] = None    # 下一页链接 CSS
    max_pages: int = 10                     # 最大翻页数

    # 请求配置
    concurrent_requests: int = 16
    download_delay: float = 0
    randomize_delay: bool = False
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)
    proxy: Optional[str] = None
    impersonate: Optional[str] = None
    use_random_ua: bool = True
    use_proxy_pool: bool = False
    proxy_pool: List[str] = field(default_factory=list)
    use_rate_limit: bool = False
    rate_limit: float = 10.0

    # 输出配置
    output_format: str = "json"
    deduplicate: bool = True

    # 自定义代码
    spider_code: Optional[str] = None


@dataclass
class DataProcessParams:
    """数据处理参数"""
    input_source: str = ""
    input_format: str = "json"
    output_format: str = "json"
    filter_rules: List[Dict] = field(default_factory=list)
    transform_rules: List[Dict] = field(default_factory=list)
    aggregate_rules: List[Dict] = field(default_factory=list)
    process_code: Optional[str] = None


@dataclass
class TaskDefinition:
    """任务定义"""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Untitled Task"
    description: str = ""
    task_type: TaskType = TaskType.CODE_EXECUTION
    user_id: Optional[str] = None
    created_by: Optional[str] = None
    code_params: Optional[CodeExecutionParams] = None
    spider_params: Optional[SpiderCrawlParams] = None
    data_params: Optional[DataProcessParams] = None
    custom_params: Dict[str, Any] = field(default_factory=dict)
    config: TaskConfig = field(default_factory=TaskConfig)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type.value,
            "user_id": self.user_id,
            "created_by": self.created_by,
            "config": {
                "timeout": self.config.timeout,
                "max_retries": self.config.max_retries,
                "retry_delay": self.config.retry_delay,
                "cpu_limit": self.config.cpu_limit,
                "memory_limit": self.config.memory_limit,
                "priority": self.config.priority.value,
                "delay": self.config.delay,
                "cron": self.config.cron,
                "depends_on": self.config.depends_on,
                "notify_on_success": self.config.notify_on_success,
                "notify_on_failure": self.config.notify_on_failure,
                "webhook_url": self.config.webhook_url,
            },
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.code_params:
            result["code_params"] = {
                "project_id": self.code_params.project_id,
                "entry_point": self.code_params.entry_point,
                "args": self.code_params.args,
                "env_vars": self.code_params.env_vars,
                "working_dir": self.code_params.working_dir,
                "python_version": self.code_params.python_version,
                "requirements": self.code_params.requirements,
            }
        if self.spider_params:
            sp = self.spider_params
            result["spider_params"] = {
                "spider_type": sp.spider_type,
                "start_urls": sp.start_urls,
                "xpath_rules": sp.xpath_rules,
                "css_rules": sp.css_rules,
                "regex_rules": sp.regex_rules,
                "list_link_xpath": sp.list_link_xpath,
                "list_link_css": sp.list_link_css,
                "pagination_xpath": sp.pagination_xpath,
                "pagination_css": sp.pagination_css,
                "max_pages": sp.max_pages,
                "concurrent_requests": sp.concurrent_requests,
                "download_delay": sp.download_delay,
                "headers": sp.headers,
                "cookies": sp.cookies,
                "proxy": sp.proxy,
                "impersonate": sp.impersonate,
                "use_random_ua": sp.use_random_ua,
                "output_format": sp.output_format,
                "spider_code": self.spider_params.spider_code,
            }
        if self.data_params:
            result["data_params"] = {
                "input_source": self.data_params.input_source,
                "input_format": self.data_params.input_format,
                "output_format": self.data_params.output_format,
                "filter_rules": self.data_params.filter_rules,
                "transform_rules": self.data_params.transform_rules,
                "process_code": self.data_params.process_code,
            }
        if self.custom_params:
            result["custom_params"] = self.custom_params
        return result


    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDefinition":
        """从字典创建"""
        config_data = data.get("config", {})
        config = TaskConfig(
            timeout=config_data.get("timeout", 3600),
            max_retries=config_data.get("max_retries", 3),
            retry_delay=config_data.get("retry_delay", 1.0),
            cpu_limit=config_data.get("cpu_limit"),
            memory_limit=config_data.get("memory_limit"),
            priority=TaskPriority(config_data.get("priority", TaskPriority.NORMAL.value)),
            delay=config_data.get("delay"),
            cron=config_data.get("cron"),
            depends_on=config_data.get("depends_on", []),
            notify_on_success=config_data.get("notify_on_success", False),
            notify_on_failure=config_data.get("notify_on_failure", True),
            webhook_url=config_data.get("webhook_url"),
        )
        code_params = None
        if "code_params" in data:
            cp = data["code_params"]
            code_params = CodeExecutionParams(
                project_id=cp["project_id"],
                entry_point=cp.get("entry_point"),
                args=cp.get("args", []),
                env_vars=cp.get("env_vars", {}),
                working_dir=cp.get("working_dir"),
                python_version=cp.get("python_version"),
                requirements=cp.get("requirements", []),
            )
        spider_params = None
        if "spider_params" in data:
            sp = data["spider_params"]
            spider_params = SpiderCrawlParams(
                spider_type=sp.get("spider_type", "custom"),
                start_urls=sp.get("start_urls", []),
                xpath_rules=sp.get("xpath_rules", {}),
                css_rules=sp.get("css_rules", {}),
                regex_rules=sp.get("regex_rules", {}),
                concurrent_requests=sp.get("concurrent_requests", 16),
                download_delay=sp.get("download_delay", 0),
                headers=sp.get("headers", {}),
                cookies=sp.get("cookies", {}),
                proxy=sp.get("proxy"),
                impersonate=sp.get("impersonate"),
                use_random_ua=sp.get("use_random_ua", True),
                output_format=sp.get("output_format", "json"),
                spider_code=sp.get("spider_code"),
            )
        data_params = None
        if "data_params" in data:
            dp = data["data_params"]
            data_params = DataProcessParams(
                input_source=dp.get("input_source", ""),
                input_format=dp.get("input_format", "json"),
                output_format=dp.get("output_format", "json"),
                filter_rules=dp.get("filter_rules", []),
                transform_rules=dp.get("transform_rules", []),
                process_code=dp.get("process_code"),
            )
        return cls(
            task_id=data.get("task_id", str(uuid.uuid4())),
            name=data.get("name", "Untitled Task"),
            description=data.get("description", ""),
            task_type=TaskType(data.get("task_type", TaskType.CODE_EXECUTION.value)),
            user_id=data.get("user_id"),
            created_by=data.get("created_by"),
            code_params=code_params,
            spider_params=spider_params,
            data_params=data_params,
            custom_params=data.get("custom_params", {}),
            config=config,
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


@dataclass
class TaskExecution:
    """任务执行记录"""
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    node_id: Optional[str] = None
    worker_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    duration_ms: float = 0
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    output_data: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    cpu_time_ms: float = 0
    memory_peak_mb: float = 0
    log_file: Optional[str] = None
    stdout_lines: int = 0
    stderr_lines: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "node_id": self.node_id,
            "worker_id": self.worker_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "error_message": self.error_message,
            "output_data": self.output_data,
            "retry_count": self.retry_count,
            "cpu_time_ms": self.cpu_time_ms,
            "memory_peak_mb": self.memory_peak_mb,
            "log_file": self.log_file,
            "stdout_lines": self.stdout_lines,
            "stderr_lines": self.stderr_lines,
        }


# ============ 优先级调度相关数据类 ============

@dataclass(order=True)
class PriorityTask:
    """优先级任务 - 用于优先级队列调度
    
    排序规则:
    1. 按 priority 升序（数值越小优先级越高）
    2. 同优先级按 enqueue_time 升序（FIFO）
    """
    priority: int                                    # 排序字段1
    enqueue_time: float = field(compare=True)        # 排序字段2
    task_id: str = field(compare=False, default="")
    project_id: str = field(compare=False, default="")
    project_type: ProjectType = field(compare=False, default=ProjectType.CODE)
    data: Dict[str, Any] = field(compare=False, default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_type": self.project_type.value,
            "priority": self.priority,
            "enqueue_time": self.enqueue_time,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PriorityTask":
        return cls(
            priority=d.get("priority", TaskPriority.NORMAL.value),
            enqueue_time=d.get("enqueue_time", 0.0),
            task_id=d.get("task_id", ""),
            project_id=d.get("project_id", ""),
            project_type=ProjectType(d.get("project_type", ProjectType.CODE.value)),
            data=d.get("data", {}),
        )


@dataclass
class TaskItem:
    """单个任务项 - Master 下发的任务格式"""
    task_id: str
    project_id: str
    project_type: ProjectType
    priority: Optional[int] = None  # 可选，不指定则使用默认优先级
    params: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    timeout: int = 3600
    download_url: Optional[str] = None
    access_token: Optional[str] = None
    file_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_type": self.project_type.value,
            "priority": self.priority,
            "params": self.params,
            "environment": self.environment,
            "timeout": self.timeout,
            "download_url": self.download_url,
            "access_token": self.access_token,
            "file_hash": self.file_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskItem":
        return cls(
            task_id=d.get("task_id", ""),
            project_id=d.get("project_id", ""),
            project_type=ProjectType(d.get("project_type", ProjectType.CODE.value)),
            priority=d.get("priority"),
            params=d.get("params", {}),
            environment=d.get("environment", {}),
            timeout=d.get("timeout", 3600),
            download_url=d.get("download_url"),
            access_token=d.get("access_token"),
            file_hash=d.get("file_hash"),
        )


@dataclass
class BatchTaskRequest:
    """批量任务请求 - Master 批量下发任务"""
    tasks: List[TaskItem]
    node_id: str
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "node_id": self.node_id,
            "batch_id": self.batch_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BatchTaskRequest":
        tasks = [TaskItem.from_dict(t) for t in d.get("tasks", [])]
        return cls(
            tasks=tasks,
            node_id=d.get("node_id", ""),
            batch_id=d.get("batch_id", str(uuid.uuid4())),
            timestamp=d.get("timestamp", datetime.now().timestamp()),
        )


@dataclass
class BatchTaskResponse:
    """批量任务响应"""
    batch_id: str
    accepted_count: int
    rejected_count: int
    accepted_tasks: List[str] = field(default_factory=list)  # task_ids
    rejected_tasks: List[Dict[str, str]] = field(default_factory=list)  # [{task_id, reason}]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "accepted_tasks": self.accepted_tasks,
            "rejected_tasks": self.rejected_tasks,
        }


@dataclass
class QueueStatus:
    """队列状态"""
    total_count: int
    by_priority: Dict[int, int] = field(default_factory=dict)      # {priority: count}
    by_project_type: Dict[str, int] = field(default_factory=dict)  # {project_type: count}
    enqueue_count: int = 0
    dequeue_count: int = 0
    avg_wait_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_count": self.total_count,
            "by_priority": self.by_priority,
            "by_project_type": self.by_project_type,
            "enqueue_count": self.enqueue_count,
            "dequeue_count": self.dequeue_count,
            "avg_wait_time_ms": self.avg_wait_time_ms,
        }
