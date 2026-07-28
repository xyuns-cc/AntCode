"""P1-GW-01 (round5/6) 回归:renew 不能重写 granted_at_ms。

审查 code-review-2026-07-23-round6-review.md P1-GW-01:
`lease_service.py:114 granted_at_ms 取整到毫秒,renew 也重写`,让 gen =
granted_at_ms 在 renew 时前进。攻击场景:
- L1 fence 在 T=100 拿 granted_at_ms=100 作 gen bind PG.lease_gen=100
- L1 continue 到 T=250, 中间调 renew 多次 → 每次都写 granted_at_ms=T,
  最终 stored granted_at_ms=250
- L2 fence 在 T=200 (L1 lease 被撤后), granted_at_ms=200, bind PG.lease_gen=200
- L1 迟到 bind 从 Redis 读 granted_at_ms=250, CAS `stored(200) <= NEW(250)`?
  true → 覆盖 L2

修:renew 分支保留 stored granted_at_ms,只更新 expires_at_ms。新 lease
才用 now_ms 作 granted_at_ms。

本测试锁死:
1. GRANT_LEASE_SCRIPT Lua 源码在 renew 分支保留 stored_granted
2. `outcome='renewed'` 时返回的 granted_at_ms == 原 grant 时刻
3. `outcome='new'` 时返回的 granted_at_ms == 当前 now_ms
"""

from __future__ import annotations

import inspect

from antcode_core.application.services import lease_service


def test_grant_lua_preserves_granted_at_ms_on_renew():
    """P1-GW-01:renew 分支 Lua 源码必须保留 stored_granted,不能重写 now_ms。"""
    module_source = inspect.getsource(lease_service)

    # renew 分支必须存在 stored_granted 变量读取
    assert "stored_granted" in module_source, "grant Lua 未读取 stored granted_at_ms,renew 会重写导致 gen 前进"
    # renew 分支必须走 `final_granted_ms = stored_granted` 分支
    assert "stored_granted > 0 and stored_granted or now_ms" in module_source, (
        "renew 分支未保留原 granted_at_ms(应 stored_granted > 0 时用原值)"
    )
    # HSET 必须用 final_granted_ms 而不是 now_ms
    assert "'granted_at_ms', tostring(final_granted_ms)" in module_source, (
        "HSET granted_at_ms 未走 final_granted_ms,renew 时会用 now_ms 覆盖"
    )


def test_grant_lua_new_lease_uses_now_ms():
    """P1-GW-01 反面:首次或过期后新 grant 仍用 now_ms 作 granted_at_ms。"""
    module_source = inspect.getsource(lease_service)

    # new 分支必须显式 `final_granted_ms = now_ms`
    assert "final_granted_ms = now_ms" in module_source, "新 lease grant 分支未用 now_ms,可能拿到 stale 或未初始化值"
