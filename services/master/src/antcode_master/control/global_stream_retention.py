"""Master 侧 global control stream 的安全裁剪（P2 §4.3）。

Worker ACL 对 ``{ns}:control:global`` 没有 XTRIM/XINFO 权限，Direct-only
部署此前无人裁剪，stream 无界增长。``trim_acknowledged_stream``
（group_name=None）取全部 consumer group 的最小安全边界，只 trim 所有
代际都已确认的条目；由 ReconcileLoop 每轮调用。
"""

from __future__ import annotations

from loguru import logger


async def trim_global_control_stream() -> None:
    from antcode_core.infrastructure.redis import get_redis_client
    from antcode_core.infrastructure.redis.control_plane import control_global_stream
    from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream

    try:
        redis = await get_redis_client()
        trimmed = await trim_acknowledged_stream(redis, control_global_stream())
        if trimmed:
            logger.info("global control stream 已裁剪 {} 条", trimmed)
    except Exception:
        logger.exception("global control stream 裁剪失败（下轮重试）")


__all__ = ["trim_global_control_stream"]
