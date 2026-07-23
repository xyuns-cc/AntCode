"""P1-GW-03 / P0-03a: legacy consumer 结算窗口边界(独立子模块)。

从 task_settle.py 拆出的 legacy 边界判定 helper,避免主文件 > 300 行。
"""

from __future__ import annotations

import os
import time

from loguru import logger


def resolve_legacy_settle_until_ts() -> int | None:
    """P1-GW-03/06 (round6): legacy consumer(裸 worker_id)结算窗口截止时间戳。

    在此时间戳之前接受 legacy consumer 的 ACK/requeue,兼容滚动升级;
    之后 legacy 分支彻底关闭,只放行 ``worker_id:lease_id`` 格式的当前代际
    consumer,避免旧代际长期借 legacy 名义结算。

    P1-GW-06 (round6) 关键修正:默认值从 0 (fail-open) 改为 None (fail-closed)。
    - None (默认/未设):legacy 通道**关闭**(生产安全默认)
    - 0 :legacy 通道**永远开启**(需要显式设,滚动升级临时窗口)
    - > 0 :截止时间戳,now < 值时开启,>= 值时关闭

    审查场景:之前 0 = 永远开启,运维忘配置 → 生产环境 legacy 通道长期
    fail-open,旧代际或攻击者可长期用裸 worker_id 越权结算。改 fail-closed
    后,滚动升级期间必须显式设 0 或未来时间戳,升级完成后自然回到关闭。

    非法字符串按 None 处理(仍 fail-closed,防错配继续开门)。
    """
    raw = os.getenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "").strip()
    if not raw:
        return None  # P1-GW-06 (round6): 未设 → fail-closed
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS 无法解析为整数,fail-closed 关闭 legacy 通道: {}",
            raw,
        )
        return None  # P1-GW-06 (round6): 非法值 → fail-closed 而不是 open


def legacy_settle_argv(worker_id: str) -> str:
    """P1-GW-03: 按 legacy_until_ts 决定是否放行 legacy consumer(裸 worker_id)。

    返回值直接用作 Lua ARGV[4]/ARGV[8]:
    - "" 空字符串:Lua 内比较不会命中(consumer 名不可能为空),等同于关闭 legacy
    - 原 worker_id:滚动升级窗口内保留旧兼容

    P1-GW-06 (round6): 未设/非法 env → until_ts=None → 走关闭分支。
    """
    until_ts = resolve_legacy_settle_until_ts()
    if until_ts is None:
        return ""  # P1-GW-06 (round6): 默认 fail-closed
    if until_ts == 0:
        return worker_id  # 显式 0 = 永远开启(滚动升级临时窗口)
    now = int(time.time())
    if now >= until_ts:
        return ""
    return worker_id
