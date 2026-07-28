"""P1-FN-02: 派发 XADD 响应丢失的查重防线。

派发流写入使用自动 Stream ID；pipeline 响应丢失时服务端可能已提交。此时
直接判失败会触发上游创建 retry run，与原消息形成双执行。本模块提供：

- 失败分类：只有"不确定失败"（超时/连接错误）才允许查重放行；
- 有界尾部查重：按派发 payload 里的确定性 ``dispatch_id``（=run_id）在
  stream 尾部 XREVRANGE 常量窗口内确认是否已提交。

残余风险与消费端兜底：极端场景（已提交却查重失败 → 判失败 → 创建
retry run）下，同 run_id 的重复消息由 Worker 本地 ``add_if_new`` 去重 +
跨 Worker run ownership fence（``run_ownership_fence``，claim 原子互斥）
抑制；run 结束后迟到的重复结果被 dispatch/runtime 终态 CAS
（``execution_status_service``）吸收，不会翻转已落库终态。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from antcode_core.infrastructure.redis.streams import StreamClient

# XADD 响应丢失后的有界查重窗口。派发写入发生在查重前的毫秒级窗口内，
# 尾部扫描该常量条数足以覆盖并发写入间隙，同时避免全量扫描。
DISPATCH_DEDUP_SCAN_COUNT = 512

# 派发消息里携带确定性去重键的字段名（值 = run_id）。
DISPATCH_ID_FIELD = "dispatch_id"


def is_indeterminate_stream_error(exc: Exception) -> bool:
    """XADD 类写入异常是否属于"不确定失败"。

    超时/连接类错误无法证明服务端未提交（命令可能已执行、仅响应丢失），
    判失败前必须先查重；参数/协议类错误是确定性失败，可直接判失败。
    内置 ``ConnectionError`` 是 ``OSError`` 子类，由 ``OSError`` 分支覆盖。
    """
    if isinstance(exc, (RedisConnectionError, RedisTimeoutError, OSError)):
        return True
    return "Connection closed" in str(exc)


def _extract_stream_field(raw_data: Any, field: str) -> str | None:
    """从 XRANGE/XREVRANGE 返回的原始 field dict 中取出字符串字段值。"""
    if not isinstance(raw_data, dict):
        return None
    raw = raw_data.get(field)
    if raw is None:
        raw = raw_data.get(field.encode("utf-8"))
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


async def find_recent_field_values(
    stream: StreamClient,
    stream_key: str,
    *,
    field: str,
    values: set[str],
    count: int = DISPATCH_DEDUP_SCAN_COUNT,
) -> set[str]:
    """在 stream 尾部最近 ``count`` 条消息里查找 ``field`` 命中 ``values`` 的值集合。"""
    if not values:
        return set()
    client = await stream._get_client()
    entries = await client.xrevrange(stream_key, max="+", min="-", count=count)
    found: set[str] = set()
    for _msg_id, raw_data in entries or []:
        value = _extract_stream_field(raw_data, field)
        if value is not None and value in values:
            found.add(value)
    return found


async def confirm_dispatch_committed(
    stream: StreamClient,
    stream_key: str,
    messages: list[dict],
    *,
    error: Exception,
) -> bool:
    """XADD 失败后判定"服务端是否其实已提交"。

    只对不确定失败做尾部有界查重；确定性失败不查，直接判失败。全部
    dispatch_id 均命中才算已提交（实践中派发批次为单条）；部分命中或查重
    本身失败（Redis 持续不可达，写入几乎必然也失败）按未提交返回。
    """
    if not is_indeterminate_stream_error(error):
        return False
    dispatch_ids = {str(message[DISPATCH_ID_FIELD]) for message in messages if message.get(DISPATCH_ID_FIELD)}
    if not dispatch_ids:
        return False
    try:
        found = await find_recent_field_values(stream, stream_key, field=DISPATCH_ID_FIELD, values=dispatch_ids)
    except Exception as verify_exc:
        logger.error("P1-FN-02: 派发查重失败(Redis 不可达),按未提交处理: stream={} err={}", stream_key, verify_exc)
        return False
    return found == dispatch_ids


__all__ = [
    "DISPATCH_DEDUP_SCAN_COUNT",
    "DISPATCH_ID_FIELD",
    "confirm_dispatch_committed",
    "find_recent_field_values",
    "is_indeterminate_stream_error",
]
