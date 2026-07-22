"""P1-GW-03 / P0-03a: legacy consumer 结算窗口边界(独立子模块)。

从 task_settle.py 拆出的 legacy 边界判定 helper,避免主文件 > 300 行。
"""

from __future__ import annotations

import os
import time

from loguru import logger


def resolve_legacy_settle_until_ts() -> int:
    """P1-GW-03: legacy consumer(裸 worker_id, 未带 lease 后缀)结算窗口截止时间戳。

    在此时间戳之前(默认无限)接受 legacy consumer 的 ACK/requeue,兼容滚动升级;
    之后 legacy 分支彻底关闭,只放行 ``worker_id:lease_id`` 格式的当前代际 consumer,
    避免旧代际长期借 legacy 名义结算。

    默认值 0 = 永远接受(与升级前行为一致);运维在完成滚动升级后应把
    ``ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS`` 设为一个已过去的 Unix 秒时间戳
    (如运维完成日期次日的 00:00)来关闭 legacy 通道。
    """
    raw = os.getenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "0").strip()
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS 无法解析为整数,保持 legacy 通道开启: {}",
            raw,
        )
        return 0


def legacy_settle_argv(worker_id: str) -> str:
    """P1-GW-03: 按 legacy_until_ts 决定是否放行 legacy consumer(裸 worker_id)。

    返回值直接用作 Lua ARGV[4]/ARGV[8]:
    - "" 空字符串:Lua 内比较不会命中(consumer 名不可能为空),等同于关闭 legacy
    - 原 worker_id:滚动升级窗口内保留旧兼容
    """
    until_ts = resolve_legacy_settle_until_ts()
    if until_ts <= 0:
        return worker_id
    now = int(time.time())
    if now >= until_ts:
        return ""
    return worker_id
