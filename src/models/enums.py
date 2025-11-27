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
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PAUSED = "paused"


class TaskType(str, Enum):
    FILE = "file"
    CODE = "code"
    RULE = "rule"
    SCRAPY = "scrapy"


class ScheduleType(str, Enum):
    ONCE = "once"
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class VenvScope(str, Enum):
    SHARED = "shared"
    PRIVATE = "private"


class InterpreterSource(str, Enum):
    MISE = "mise"
    LOCAL = "local"
