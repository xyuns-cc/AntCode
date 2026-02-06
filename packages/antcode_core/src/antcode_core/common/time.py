"""时间工具模块

提供时间相关的工具函数：
- UTC 时间获取
- 时间戳转换
- 时区转换
"""

from datetime import UTC, datetime

import pytz

# 默认时区
DEFAULT_TIMEZONE = "Asia/Shanghai"


def now_utc() -> datetime:
    """获取当前 UTC 时间

    Returns:
        带时区信息的 UTC datetime
    """
    return datetime.now(UTC)


def now_local(tz: str = DEFAULT_TIMEZONE) -> datetime:
    """获取当前本地时间

    Args:
        tz: 时区名称，默认 Asia/Shanghai

    Returns:
        带时区信息的本地 datetime
    """
    local_tz = pytz.timezone(tz)
    return datetime.now(local_tz)


def timestamp_ms() -> int:
    """获取当前时间戳（毫秒）

    Returns:
        毫秒级时间戳
    """
    return int(datetime.now(UTC).timestamp() * 1000)


def timestamp_sec() -> int:
    """获取当前时间戳（秒）

    Returns:
        秒级时间戳
    """
    return int(datetime.now(UTC).timestamp())


def utc_to_local(dt: datetime, tz: str = DEFAULT_TIMEZONE) -> datetime:
    """UTC 时间转本地时间

    Args:
        dt: UTC datetime
        tz: 目标时区名称

    Returns:
        本地时间 datetime
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local_tz = pytz.timezone(tz)
    return dt.astimezone(local_tz)


def local_to_utc(dt: datetime, tz: str = DEFAULT_TIMEZONE) -> datetime:
    """本地时间转 UTC 时间

    Args:
        dt: 本地 datetime
        tz: 源时区名称

    Returns:
        UTC datetime
    """
    if dt.tzinfo is None:
        local_tz = pytz.timezone(tz)
        dt = local_tz.localize(dt)
    return dt.astimezone(UTC)


def from_timestamp(ts: int | float, unit: str = "s") -> datetime:
    """从时间戳创建 datetime

    Args:
        ts: 时间戳
        unit: 单位，"s" 秒或 "ms" 毫秒

    Returns:
        UTC datetime
    """
    if unit == "ms":
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=UTC)


def to_timestamp(dt: datetime, unit: str = "s") -> int:
    """datetime 转时间戳

    Args:
        dt: datetime 对象
        unit: 单位，"s" 秒或 "ms" 毫秒

    Returns:
        时间戳
    """
    ts = dt.timestamp()
    if unit == "ms":
        return int(ts * 1000)
    return int(ts)


def format_datetime(
    dt: datetime,
    fmt: str = "%Y-%m-%d %H:%M:%S",
    tz: str | None = None,
) -> str:
    """格式化 datetime

    Args:
        dt: datetime 对象
        fmt: 格式字符串
        tz: 可选的目标时区

    Returns:
        格式化后的字符串
    """
    if tz:
        dt = utc_to_local(dt, tz) if dt.tzinfo == UTC else dt
    return dt.strftime(fmt)


def parse_datetime(
    s: str,
    fmt: str = "%Y-%m-%d %H:%M:%S",
    tz: str | None = None,
) -> datetime:
    """解析 datetime 字符串

    Args:
        s: datetime 字符串
        fmt: 格式字符串
        tz: 可选的时区

    Returns:
        datetime 对象
    """
    dt = datetime.strptime(s, fmt)
    if tz:
        local_tz = pytz.timezone(tz)
        dt = local_tz.localize(dt)
    return dt
