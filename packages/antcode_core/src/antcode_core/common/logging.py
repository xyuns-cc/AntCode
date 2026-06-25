"""日志配置模块

提供日志初始化、格式化和敏感信息脱敏功能。

P5.4: 集成 W3C TraceContext —— 每条日志会自动从当前 ContextVar
(``antcode_core.observability.tracing.get_current_trace_id``) 提取
``trace_id`` 注入到 ``record["extra"]["trace_id"]``,日志格式串里通过
``{extra[trace_id]}`` 渲染。Worker 在每个任务的 ``_worker_loop`` 中
调用 ``set_current_trace`` 后,该任务内所有日志会自动带上 trace_id,
方便从单条日志反查整个分布式调用链。
"""

import os
import re
import sys
from typing import Any

from loguru import logger

from antcode_core.common.config import settings

# 日志格式
# ``extra[trace_id]`` 在未绑定 trace 时由 ``_inject_trace_id`` 填一个 "-"
# 占位,避免 loguru 渲染时报 KeyError。
CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>trace={extra[trace_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | trace={extra[trace_id]} | "
    "{name}:{function}:{line} - {message}"
)

# 敏感字段模式（用于脱敏）
#
# 列表里的正则按顺序逐条 sub,顺序排前的优先匹配。补齐了 P1-#L8 提到的几类:
# - JSON 序列化的 "api_key" / "token" 字面量(双引号包裹的 value)
# - Authorization Bearer xxx 头
# - AntCode 内部 ``ak_xxxx`` API key 前缀(generate_api_key 的格式)
SENSITIVE_PATTERNS = [
    # JSON 序列化的 api_key 字面量: "api_key": "xxx"
    (
        re.compile(r'("api[_-]?key"\s*:\s*")[^"]+(")', re.IGNORECASE),
        r"\1***\2",
    ),
    # JSON 序列化的 token / access_token / refresh_token 字面量
    (
        re.compile(r'("(?:access_|refresh_)?token"\s*:\s*")[^"]+(")', re.IGNORECASE),
        r"\1***\2",
    ),
    # JSON 序列化的 secret / password 字面量
    (
        re.compile(r'("(?:secret|password|passwd|pwd|install_key|private_key)"\s*:\s*")[^"]+(")', re.IGNORECASE),
        r"\1***\2",
    ),
    # Authorization header: Bearer xxx / Basic xxx
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
        "Bearer ***",
    ),
    (
        re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+", re.IGNORECASE),
        "Basic ***",
    ),
    # AntCode API key 前缀 ak_xxxxxxxx
    (
        re.compile(r"\bak_[A-Za-z0-9_\-]{8,}"),
        "ak_***",
    ),
    # API 密钥/私密密钥（key=value 格式)
    (
        re.compile(
            r'(api[_-]?key|secret[_-]?key|access[_-]?key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{8,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 密码
    (
        re.compile(
            r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^"\'\s,}]{3,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 令牌(通用 key=value)
    (
        re.compile(
            r'(token|jwt)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-\.]{20,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 授权头: Authorization=xxx / Authorization: xxx
    (
        re.compile(
            r'(Authorization)["\']?\s*[:=]\s*["\']?(?:Bearer\s+|Basic\s+)?([a-zA-Z0-9_\-\.+/=]{8,})["\']?',
            re.IGNORECASE,
        ),
        r"\1=***REDACTED***",
    ),
    # 含密码的数据库 URL
    (
        re.compile(r"(mysql|postgres|postgresql|redis)://([^:]+):([^@]+)@", re.IGNORECASE),
        r"\1://\2:***@",
    ),
    # 邮箱（部分脱敏）
    (re.compile(r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"), r"***@\2"),
]


# 字典脱敏:命中即整体 value 替换为 ``"***"`` 的 key 名(子串匹配)。
# 给 web_api access log middleware 直接调用,避免在请求体里漏字段。
DEFAULT_SENSITIVE_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "secret_key",
        "secretkey",
        "access_key",
        "accesskey",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "jwt",
        "cert",
        "certificate",
        "private_key",
        "install_key",
    }
)


def sanitize_log_message(message: str) -> str:
    """对日志消息进行敏感信息脱敏"""
    if not message:
        return message

    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def sanitize_dict(
    data: dict[str, Any],
    sensitive_keys: set[str] | frozenset[str] | None = None,
    mask: str = "***",
) -> dict[str, Any]:
    """对字典数据进行敏感信息脱敏。

    命中 ``sensitive_keys`` 的 key,value 整体替换为 ``mask``;否则递归
    处理子 dict / list。给 web_api access log middleware 与日志过滤器
    复用,确保结构化字段不会因正则漏匹配而泄露。
    """
    if sensitive_keys is None:
        sensitive_keys = DEFAULT_SENSITIVE_KEY_TOKENS

    if not isinstance(data, dict):
        return data

    result: dict[str, Any] = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(sk in key_lower for sk in sensitive_keys):
            result[key] = mask
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, sensitive_keys, mask=mask)
        elif isinstance(value, list):
            result[key] = [
                sanitize_dict(item, sensitive_keys, mask=mask)
                if isinstance(item, dict)
                else (sanitize_log_message(item) if isinstance(item, str) else item)
                for item in value
            ]
        elif isinstance(value, str):
            result[key] = sanitize_log_message(value)
        else:
            result[key] = value

    return result


def _inject_trace_id(record: dict[str, Any]) -> None:
    """把 ContextVar 中的当前 trace_id 注入到 ``record['extra']``。

    若未绑定 trace,填一个 ``"-"`` 占位,避免 loguru 在格式串里渲染
    ``{extra[trace_id]}`` 时报 KeyError(每条日志都要可渲染)。
    显式调用 ``logger.bind(trace_id=...)`` 已经手动设置过的会优先保留。
    """
    extra = record.setdefault("extra", {})
    if extra.get("trace_id"):
        return
    try:
        # 延迟导入以打破潜在的循环依赖(observability 不应依赖 common)。
        from antcode_core.observability.tracing import get_current_trace_id

        trace_id = get_current_trace_id()
    except Exception:  # noqa: BLE001 - 日志路径绝不能因可观测性故障崩
        trace_id = None
    extra["trace_id"] = trace_id or "-"


class SanitizingFilter:
    """日志脱敏过滤器(同时承担 trace_id 注入)"""

    def __call__(self, record: dict[str, Any]) -> bool:
        """对日志记录进行脱敏处理 + trace_id 注入。

        顺序: 先注入 trace_id(它不属于敏感字段),再脱敏。这样即便
        sanitize_dict 后续把 extra 整个替换,trace_id 也已经在新 dict 里。
        """
        # 注入 trace_id(在脱敏前,避免被当成"未知键"丢掉)
        _inject_trace_id(record)

        # 脱敏消息内容
        if "message" in record:
            record["message"] = sanitize_log_message(record["message"])

        # 脱敏 extra 字段
        if "extra" in record and isinstance(record["extra"], dict):
            record["extra"] = sanitize_dict(record["extra"])

        # 脱敏后再兜底一次(sanitize_dict 不会动 trace_id 这种非敏感键,
        # 但万一 extra 被替换为新 dict 而丢失 trace_id 仍可补上)
        record["extra"].setdefault("trace_id", "-")

        return True


def setup_logging(
    level: str | None = None,
    log_to_file: bool | None = None,
    log_file_path: str | None = None,
) -> None:
    """初始化日志系统，包含敏感信息脱敏

    Args:
        level: 日志级别，默认使用 settings.LOG_LEVEL
        log_to_file: 是否输出到文件，默认使用 settings.LOG_TO_FILE
        log_file_path: 日志文件路径，默认使用 settings.LOG_FILE_PATH
    """
    logger.remove()

    # 使用参数或配置
    log_level = level or settings.LOG_LEVEL
    should_log_to_file = log_to_file if log_to_file is not None else settings.LOG_TO_FILE
    file_path = log_file_path or settings.LOG_FILE_PATH

    # 创建脱敏过滤器
    sanitizing_filter = SanitizingFilter()

    # 控制台输出（带脱敏）
    logger.add(
        sys.stderr,
        format=CONSOLE_FORMAT,
        level=log_level,
        colorize=True,
        filter=sanitizing_filter,
    )

    # 文件输出（带脱敏）
    if should_log_to_file:
        log_dir = os.path.dirname(file_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        logger.add(
            file_path,
            format=FILE_FORMAT,
            level=log_level,
            rotation="500 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            enqueue=True,  # 异步写入，提升性能
            filter=sanitizing_filter,
        )

    logger.info(
        f"日志初始化完成: level={log_level}, file={should_log_to_file}, sanitize=True"
    )


def get_logger(name: str | None = None):
    """获取 logger 实例

    Args:
        name: logger 名称，用于区分不同模块的日志

    Returns:
        loguru logger 实例
    """
    if name:
        return logger.bind(name=name)
    return logger
