import os
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def _env_list(name: str, default: str, separator: str = ",") -> list[str]:
    value = os.getenv(name)
    source = value if value is not None else default
    return [item.strip() for item in source.split(separator) if item.strip()]


_BASE_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

# 节点标识与基础信息
SCRAPY_NODE_NAME = os.getenv("SCRAPY_NODE_NAME", "Antcode-Worker")
SCRAPY_TIMEZONE = os.getenv("SCRAPY_TIMEZONE", "Asia/Shanghai")

# Scrapy settings
BOT_NAME = os.getenv("SCRAPY_BOT_NAME", "spider")

SPIDER_MODULES = ["spider.spiders"]
NEWSPIDER_MODULE = "spider.spiders"

ADDONS = {}

# Crawl responsibly
ROBOTSTXT_OBEY = _env_bool("SCRAPY_ROBOTSTXT_OBEY", False)

# Concurrency & throttling
CONCURRENT_REQUESTS_PER_DOMAIN = _env_int("SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN", 1)
DOWNLOAD_DELAY = _env_float("SCRAPY_DOWNLOAD_DELAY", 0.5)

SCHEDULER = "scrapy_redis.scheduler.Scheduler"
DUPEFILTER_CLASS = "scrapy_redis.dupefilter.RFPDupeFilter"
SCHEDULER_PERSIST = _env_bool("SCRAPY_SCHEDULER_PERSIST", True)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#    "Accept-Language": "en",
#}

# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    "spider.middlewares.SpiderSpiderMiddleware": 543,
#}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
DOWNLOADER_MIDDLEWARES = {
    "spider.middlewares.ProxyMiddleware.ProxyMiddleware": 100,  # 优先级要高，确保在其他中间件之前设置代理
    "spider.middlewares.UserAgentMiddleware.UserAgentMiddleware": 400,  # 在默认UA中间件之前
    "spider.middlewares.CurlCffiMiddleware.CurlCffiMiddleware": 530,
    "spider.middlewares.DrissionPageMiddleware.DrissionPageMiddleware": 543,  # 启用DrissionPage中间件
}

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
EXTENSIONS = {
    "spider.monitoring.MonitoringExtension": 500,
    # "scrapy.extensions.telnet.TelnetConsole": None,
}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    "spider.pipelines.kafka_pipeline.KafkaPipeline": 300,
    # 或使用带缓冲的版本以提高性能
    # "spider.pipelines.kafka_pipeline.BufferedKafkaPipeline": 300,
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
#AUTOTHROTTLE_ENABLED = True
# The initial download delay
#AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
#AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
#AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = "httpcache"
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
FEED_EXPORT_ENCODING = "utf-8"

# DrissionPage中间件配置
DRISSIONPAGE_ENABLED = _env_bool("DRISSIONPAGE_ENABLED", True)
DRISSIONPAGE_HEADLESS = _env_bool("DRISSIONPAGE_HEADLESS", True)
DRISSIONPAGE_WINDOW_SIZE = os.getenv("DRISSIONPAGE_WINDOW_SIZE", "1920,1080")
DRISSIONPAGE_WAIT_TIME = _env_int("DRISSIONPAGE_WAIT_TIME", 3)
DRISSIONPAGE_PAGE_LOAD_TIMEOUT = _env_int("DRISSIONPAGE_PAGE_LOAD_TIMEOUT", 30)
DRISSIONPAGE_RETRY = _env_int("DRISSIONPAGE_RETRY", 0)
DRISSIONPAGE_BROWSER_PATH = os.getenv("DRISSIONPAGE_BROWSER_PATH")
DRISSIONPAGE_USER_DATA_PATH = os.getenv("DRISSIONPAGE_USER_DATA_PATH")
DRISSIONPAGE_ARGUMENTS = _env_list(
    "DRISSIONPAGE_ARGUMENTS",
    "--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled,"
    "--disable-extensions,--disable-plugins,--disable-gpu,--disable-software-rasterizer,"
    "--disable-logging,--ignore-certificate-errors",
)

# Loguru日志配置
LOG_ENABLED = _env_bool("SCRAPY_LOG_ENABLED", True)
LOG_LEVEL = os.getenv("SCRAPY_LOG_LEVEL", "INFO")
LOG_FORMAT = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
LOG_ROTATION = "500 MB"  # 日志文件轮转大小
LOG_RETENTION = "10 days"  # 日志保留时间
LOG_COMPRESSION = "zip"  # 日志压缩格式
LOG_ENCODING = "utf-8"  # 日志编码
LOG_ENQUEUE = True  # 异步写入
LOG_BACKTRACE = True  # 是否显示异常回溯
LOG_DIAGNOSE = True  # 是否显示变量值
LOG_COLORIZE = True  # 控制台输出是否使用颜色
LOG_SERIALIZE = False  # 是否序列化为JSON格式

# curl_cffi 中间件配置
CURL_CFFI_ENABLED = _env_bool("CURL_CFFI_ENABLED", True)
CURL_CFFI_IMPERSONATE = os.getenv("CURL_CFFI_IMPERSONATE", "chrome120")
CURL_CFFI_TIMEOUT = _env_int("CURL_CFFI_TIMEOUT", 30)
CURL_CFFI_VERIFY = _env_bool("CURL_CFFI_VERIFY", True)
CURL_CFFI_ALLOW_REDIRECTS = _env_bool("CURL_CFFI_ALLOW_REDIRECTS", True)
CURL_CFFI_RETRY = _env_int("CURL_CFFI_RETRY", 1)

# 日志文件路径配置
LOG_DIR = "logs"  # 日志目录
LOG_FILE_NAME = "spider_{time:YYYY-MM-DD}.log"  # 日志文件名格式
LOG_ERROR_FILE_NAME = "spider_error_{time:YYYY-MM-DD}.log"  # 错误日志文件名

DATA_STORE_TYPE = os.getenv("DATA_STORE_TYPE", "kafka")

# Kafka配置（当 DATA_STORE_TYPE=kafka 时生效）
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC_LIST = os.getenv("KAFKA_TOPIC_LIST", "spider_list_data")
KAFKA_TOPIC_DETAIL = os.getenv("KAFKA_TOPIC_DETAIL", "spider_detail_data")
KAFKA_TOPIC_ERROR = os.getenv("KAFKA_TOPIC_ERROR", "spider_error_data")

# SSL配置（如果使用SSL）
# KAFKA_SSL_CAFILE = '/path/to/ca-cert'  # CA证书文件路径
# KAFKA_SSL_CERTFILE = '/path/to/client-cert'  # 客户端证书文件路径
# KAFKA_SSL_KEYFILE = '/path/to/client-key'  # 客户端密钥文件路径
