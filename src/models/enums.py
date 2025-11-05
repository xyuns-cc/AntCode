"""
枚举类型定义
定义项目相关的所有枚举类型
"""

from enum import Enum


class ProjectType(str, Enum):
    """项目类型"""
    FILE = "file"          # 文件项目
    RULE = "rule"          # 规则项目
    CODE = "code"          # 代码项目


class ProjectStatus(str, Enum):
    """项目状态"""
    DRAFT = "draft"        # 草稿
    ACTIVE = "active"      # 活跃
    INACTIVE = "inactive"  # 非活跃
    ARCHIVED = "archived"  # 已归档


class CrawlEngine(str, Enum):
    """采集引擎"""
    BROWSER = "browser"                  # 浏览器引擎（Selenium/Playwright等）
    REQUESTS = "requests"                # Requests HTTP库
    CURL_CFFI = "curl_cffi"             # curl_cffi库（模拟curl请求）


class PaginationType(str, Enum):
    """翻页类型"""
    NONE = "none"                            # 无翻页
    URL_PATTERN = "url_pattern"              # URL模式翻页
    CLICK_ELEMENT = "click_element"          # 元素点击翻页
    URL_PARAM = "url_param"                  # URL参数翻页
    JAVASCRIPT = "javascript"                # JS点击翻页
    AJAX = "ajax"                            # AJAX加载
    INFINITE_SCROLL = "infinite_scroll"      # 无限滚动


class RuleType(str, Enum):
    """规则类型"""
    XPATH = "xpath"                      # XPath选择器
    CSS = "css"                          # CSS选择器
    REGEX = "regex"                      # 正则表达式
    JSONPATH = "jsonpath"                # JSONPath表达式


class CallbackType(str, Enum):
    """回调类型"""
    LIST = "list"                        # 列表页回调
    DETAIL = "detail"                    # 详情页回调
    MIXED = "mixed"                      # 混合模式（同时包含列表页和详情页规则）


class RequestMethod(str, Enum):
    """请求方法"""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"          # 待执行
    RUNNING = "running"          # 执行中
    SUCCESS = "success"          # 成功
    FAILED = "failed"            # 失败
    CANCELLED = "cancelled"      # 已取消
    TIMEOUT = "timeout"          # 超时
    PAUSED = "paused"           # 已暂停
class TaskType(str, Enum):
    """任务类型"""
    FILE = "file"                # 文件项目任务
    CODE = "code"                # 代码项目任务
    RULE = "rule"                # 规则项目任务
    SCRAPY = "scrapy"            # Scrapy爬虫任务
class ScheduleType(str, Enum):
    """调度类型"""
    ONCE = "once"                # 一次性
    CRON = "cron"                # Cron表达式
    INTERVAL = "interval"        # 间隔执行
    DATE = "date"                # 指定时间


class VenvScope(str, Enum):
    """虚拟环境作用域"""
    SHARED = "shared"     # 公共虚拟环境（可被多个项目复用）
    PRIVATE = "private"   # 私有虚拟环境（项目专属）


class InterpreterSource(str, Enum):
    """解释器来源"""
    MISE = "mise"      # 由 mise 管理安装
    LOCAL = "local"    # 系统本地（用户提供路径）
