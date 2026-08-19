"""
枚举定义

所有业务枚举类型的集中定义。
"""

from enum import Enum, StrEnum

# ========== 项目相关枚举 ==========


class ProjectType(StrEnum):
    """项目类型"""

    FILE = "file"
    RULE = "rule"
    CODE = "code"


class ProjectStatus(StrEnum):
    """项目状态"""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class CrawlEngine(StrEnum):
    """爬取引擎。

    S1 (Scrapy 迁移): 新增 ``playwright`` 用于 JS 渲染站点。Scrapy 侧根据
    此值动态装配 ``DOWNLOAD_HANDLERS``：
    - requests → Scrapy 内置 HTTP handler
    - curl_cffi → scrapy-impersonate（TLS/JA3 指纹伪装）
    - playwright → scrapy-playwright（浏览器渲染）
    值必须是字符串枚举，DB 迁移不需要（CharEnumField 存字面量）。
    """

    REQUESTS = "requests"
    CURL_CFFI = "curl_cffi"
    PLAYWRIGHT = "playwright"


class PaginationType(StrEnum):
    """分页类型"""

    NONE = "none"
    URL_PATTERN = "url_pattern"
    CLICK_ELEMENT = "click_element"
    URL_PARAM = "url_param"
    JAVASCRIPT = "javascript"
    AJAX = "ajax"
    INFINITE_SCROLL = "infinite_scroll"


class RuleType(StrEnum):
    """规则类型"""

    XPATH = "xpath"
    CSS = "css"
    REGEX = "regex"


class CallbackType(StrEnum):
    """回调类型"""

    LIST = "list"
    DETAIL = "detail"
    MIXED = "mixed"


class RequestMethod(StrEnum):
    """HTTP 请求方法"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


# ========== 任务相关枚举 ==========


class TaskStatus(StrEnum):
    """任务状态"""

    PENDING = "pending"  # 等待调度
    DISPATCHING = "dispatching"  # 正在分配节点
    QUEUED = "queued"  # 已分发到节点队列，等待执行
    RUNNING = "running"  # 正在执行
    SUCCESS = "success"  # 执行成功
    FAILED = "failed"  # 执行失败
    CANCELLED = "cancelled"  # 已取消
    TIMEOUT = "timeout"  # 执行超时
    PAUSED = "paused"  # 已暂停
    REJECTED = "rejected"  # 节点拒绝
    SKIPPED = "skipped"  # 已跳过


class DispatchStatus(StrEnum):
    """分发状态"""

    PENDING = "pending"  # 等待分发
    DISPATCHING = "dispatching"  # 分发中
    DISPATCHED = "dispatched"  # 已发送
    ACKED = "acked"  # 节点确认
    REJECTED = "rejected"  # 节点拒绝
    TIMEOUT = "timeout"  # 确认超时
    FAILED = "failed"  # 分发失败


class RuntimeStatus(StrEnum):
    """运行时状态"""

    QUEUED = "queued"  # 节点队列等待
    RUNNING = "running"  # 正在执行
    SUCCESS = "success"  # 执行成功
    FAILED = "failed"  # 执行失败
    CANCELLED = "cancelled"  # 已取消
    TIMEOUT = "timeout"  # 执行超时
    SKIPPED = "skipped"  # 已跳过


class TaskType(StrEnum):
    """任务类型"""

    FILE = "file"
    CODE = "code"
    RULE = "rule"
    SPIDER = "spider"  # 通过 Worker 节点执行的爬虫任务


class ScheduleType(StrEnum):
    """调度类型"""

    ONCE = "once"
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class ExecutionStrategy(StrEnum):
    """执行策略"""

    FIXED_WORKER = "fixed"  # 固定 Worker（仅在绑定 Worker 执行，不可用时失败）
    SPECIFIED = "specified"  # 指定 Worker（任务级别指定）
    AUTO_SELECT = "auto"  # 自动选择（负载均衡）
    PREFER_BOUND = "prefer"  # 优先绑定 Worker（不可用时自动选择其他 Worker）


# ========== 运行时环境相关枚举 ==========


class RuntimeKind(StrEnum):
    """可被项目绑定的运行时类型。

    只有 python 一种：运行时是 Worker 上按 python_version 现装的 venv
    (RuntimeControlService.create_env 的入参就只有 python_version)，而
    Node/Go/Java 是镜像构建期预装进 PATH 的固定工具链，没有任何"按项目创建"
    的路径。曾经存在的 JAVA / GO 成员没有任何写入方，只是给 project.language
    之外又留了一个可分叉的语言真源；执行语言现由
    ``antcode_contracts.execution_language`` 单独承载。
    """

    PYTHON = "python"


class RuntimeScope(StrEnum):
    """运行时环境作用域"""

    SHARED = "shared"
    PRIVATE = "private"


class RuntimeLocation(StrEnum):
    """运行时环境位置"""

    WORKER = "worker"  # Worker


# ========== Worker 节点相关枚举 ==========


class WorkerStatus(StrEnum):
    """Worker 节点状态"""

    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    CONNECTING = "connecting"


class WorkerPermission(StrEnum):
    """用户对 Worker 的授权级别。"""

    VIEW = "view"
    USE = "use"


# ========== 审计日志相关枚举 ==========


class AuditAction(StrEnum):
    """审计操作类型"""

    # 用户相关
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGE = "password_change"

    # 用户管理
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_ROLE_CHANGE = "user_role_change"

    # 项目相关
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_DELETE = "project_delete"

    # 任务相关
    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    TASK_DELETE = "task_delete"
    TASK_EXECUTE = "task_execute"
    TASK_STOP = "task_stop"
    REDISPATCH_GIVE_UP = "redispatch_give_up"

    # Worker 相关
    WORKER_CREATE = "worker_create"
    WORKER_UPDATE = "worker_update"
    WORKER_DELETE = "worker_delete"
    WORKER_RESOURCE_UPDATE = "worker_resource_update"

    # 系统配置
    CONFIG_UPDATE = "config_update"
    ALERT_CONFIG_UPDATE = "alert_config_update"

    # 环境管理
    ENV_CREATE = "env_create"
    ENV_DELETE = "env_delete"

    # 其他
    EXPORT_DATA = "export_data"
    IMPORT_DATA = "import_data"


# ========== 爬取批次相关枚举 ==========


class BatchStatus(StrEnum):
    """批次状态"""

    PENDING = "pending"  # 等待开始
    RUNNING = "running"  # 运行中
    PAUSED = "paused"  # 已暂停
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


class Priority(int, Enum):
    """任务优先级"""

    HIGH = 0  # 高优先级
    NORMAL = 5  # 普通优先级
    LOW = 9  # 低优先级


__all__ = [
    # 项目相关
    "ProjectType",
    "ProjectStatus",
    "CrawlEngine",
    "PaginationType",
    "RuleType",
    "CallbackType",
    "RequestMethod",
    # 任务相关
    "TaskStatus",
    "DispatchStatus",
    "RuntimeStatus",
    "TaskType",
    "ScheduleType",
    "ExecutionStrategy",
    # 运行时环境相关
    "RuntimeKind",
    "RuntimeScope",
    "RuntimeLocation",
    # Worker 节点相关
    "WorkerStatus",
    "WorkerPermission",
    # 审计日志相关
    "AuditAction",
    # 爬取批次相关
    "BatchStatus",
    "Priority",
]
