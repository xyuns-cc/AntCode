"""轮换前的重投队列排空门禁。

与 ``test_global_encryption_key_rotation`` 的分工：那边钉「密文怎么重写」，
这边钉「什么时候不许开始重写」——队列里还压着用旧密钥加密的载荷时轮换密钥，
那些载荷之后谁都解不开。两者失效模式不同：那边失效是数据写坏，这边失效是
数据静默失联。
"""

from __future__ import annotations

import pytest
from antcode_core.application.services.security.redispatch_rotation_guard import (
    inspect_redispatch_drain,
    require_redispatch_drained,
)

#: 三个非空键的条目总数（zset 2 + hash 1 + 旧命名空间 zset 3）。
EXPECTED_NON_EMPTY_REDISPATCH_ENTRIES = 6


class _Redis:
    def __init__(self, counts: dict[tuple[str, str], int]) -> None:
        self.counts = counts

    async def zcard(self, key: str) -> int:
        return self.counts.get(("zset", key), 0)

    async def hlen(self, key: str) -> int:
        return self.counts.get(("hash", key), 0)


@pytest.mark.asyncio
async def test_redispatch_guard_checks_current_and_legacy_keys() -> None:
    """带 hash tag 的新键与不带的旧键都要数进来，漏掉哪一边都会放行未排空的队列。"""
    namespace = "tenant-a"
    current = "{tenant-a}:task:redispatch"
    legacy = "tenant-a:task:redispatch"
    state = await inspect_redispatch_drain(
        _Redis({("zset", current): 2, ("hash", f"{current}:processing"): 1, ("zset", legacy): 3}),
        namespace,
    )

    assert state.total == EXPECTED_NON_EMPTY_REDISPATCH_ENTRIES
    with pytest.raises(RuntimeError, match="禁止轮换"):
        require_redispatch_drained(state)


@pytest.mark.asyncio
async def test_redispatch_guard_accepts_only_fully_drained_queue() -> None:
    """反向判据：全空才放行。只有拒绝用例时，「恒拒绝」也能拿满分。"""
    state = await inspect_redispatch_drain(_Redis({}), "tenant-a")
    require_redispatch_drained(state)
    assert state.total == 0
