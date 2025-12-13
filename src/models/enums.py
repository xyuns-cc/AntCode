"""枚举定义"""

from enum import Enum


class ProjectType(str, Enum):
    FILE = "file"
    RULE = "rule"
    CODE = "code"


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class CrawlEngine(str, Enum):
    BROWSER = "browser"
    REQUESTS = "requests"
    CURL_CFFI = "curl_cffi"


class PaginationType(str, Enum):
    NONE = "none"
    URL_PATTERN = "url_pattern"
    CLICK_ELEMENT = "click_element"
    URL_PARAM = "url_param"
    JAVASCRIPT = "javascript"
    AJAX = "ajax"
    INFINITE_SCROLL = "infinite_scroll"


class RuleType(str, Enum):
    XPATH = "xpath"
    CSS = "css"
    REGEX = "regex"
    JSONPATH = "jsonpath"


class CallbackType(str, Enum):
    LIST = "list"
    DETAIL = "detail"
    MIXED = "mixed"


class RequestMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


class TaskStatus(str, Enum):
    PENDING = "pending"           # 等待调度
    DISPATCHING = "dispatching"   # 正在分配节点
    QUEUED = "queued"             # 已分发到节点队列，等待执行
    RUNNING = "running"           # 正在执行
    SUCCESS = "success"           # 执行成功
    FAILED = "failed"             # 执行失败
    CANCELLED = "cancelled"       # 已取消
    TIMEOUT = "timeout"           # 执行超时
    PAUSED = "paused"             # 已暂停


class TaskType(str, Enum):
    FILE = "file"
    CODE = "code"
    RULE = "rule"
    SPIDER = "spider"  # 通过 Worker 节点执行的爬虫任务


class ScheduleType(str, Enum):
    ONCE = "once"
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class VenvScope(str, Enum):
    SHARED = "shared"
    PRIVATE = "private"


class EnvLocation(str, Enum):
    """环境位置"""
    LOCAL = "local"   # 本地（主节点）
    NODE = "node"     # Worker节点


class InterpreterSource(str, Enum):
    MISE = "mise"
    LOCAL = "local"


class ExecutionStrategy(str, Enum):
    """执行策略枚举"""
    LOCAL = "local"           # 本地执行（主节点）
    FIXED_NODE = "fixed"      # 固定节点（仅在绑定节点执行，不可用时失败）
    SPECIFIED = "specified"   # 指定节点（任务级别指定）
    AUTO_SELECT = "auto"      # 自动选择（负载均衡）
    PREFER_BOUND = "prefer"   # 优先绑定节点（不可用时自动选择其他节点）
