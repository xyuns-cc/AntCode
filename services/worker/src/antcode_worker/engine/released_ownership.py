"""区分「run fence 是我自己放的」与「run fence 被别人抢走了」。

背景：`run_ownership_fence_lua._RENEW_SCRIPT` 对「key 不存在」和「key 里是别人的
token」返回同一个 `0`，上层一路收敛成 `renewed=False`（`direct_control.renew_run_
ownership` / gateway `RenewRunOwnership` 都只回一个 bool）。协议层因此**无法**区分
这两种情形，而它们的正确处置恰好相反：

- 被别人抢走 / 自己的租约过期 → 真的失去了 fence，必须进程级 self-fence（fail-closed）；
- 自己刚正常结算完并主动 release 掉 → 完全正常，不该杀进程。

能区分二者的只有本进程：release 是本地可知事件。本账本就只记这一件事。

为什么需要它：`Engine._renew_active_run_ownership` 遍历的是 `StateManager.get_all()`
的**快照**，而每 renew 一个 run 都要 await 一次网络往返。快照里的另一个 run 完全可能
在这期间跑完 `finish_settlement`（从 `_runs` 弹出）+ `release`，轮到它时 renew 必然
返回 False。单任务串行时这个窗口极小；爬取批次启动即并发派发全部种子，撞上是必然的。
"""

from __future__ import annotations


class ReleasedOwnershipLedger:
    """记录本引擎主动释放过 fence 的 run id。

    生命周期只需覆盖「一轮续租」：`Engine._report_result` 里 `finish_settlement`
    （把 run 弹出 `_runs`）严格早于 `release`，所以任何进了账本的 run 都已经不在
    `_runs` 里了——下一轮续租重新 `get_all()` 时不可能再取到它。因此在每轮续租开始
    时清空即可，既不会漏放行（本轮内的 release 都记在账上），也不会无限增长。
    """

    def __init__(self) -> None:
        self._released: set[str] = set()

    def begin_renewal_pass(self) -> None:
        """开始新一轮续租：上一轮的快照已经作废，账本随之清空。"""
        self._released.clear()

    def record_release(self, run_id: str) -> None:
        """登记一次主动释放。必须在真正调用 release 之前登记，否则存在窗口。"""
        self._released.add(run_id)

    def forget(self, run_id: str) -> None:
        """run 被重新 claim：它又归我了，之前那笔释放记录作废。"""
        self._released.discard(run_id)

    def was_released_by_self(self, run_id: str) -> bool:
        """本轮续租期间，这个 run 的 fence 是不是我自己放掉的。"""
        return run_id in self._released


__all__ = ["ReleasedOwnershipLedger"]
