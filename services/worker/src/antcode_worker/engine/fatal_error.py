"""引擎致命错误信号的对外接口。

进程级 self-fence 的两端放在一起：``record_fatal_error`` 是写入端（引擎内部
或 lease 续期循环等外部协作方上报），``wait_for_fatal_error`` 是读取端（应用
主循环据此停机）。

Worker 带着失效租约继续执行 run 会被 master 判死并补派，造成同一个 run 双执行，
因此任何"本进程已不再安全"的判定都必须能走到这条通道上。
"""

from __future__ import annotations

from antcode_worker.engine.ownership_fence import FatalErrorSignal


class FatalErrorMixin:
    """由 ``Engine`` 混入；``_fatal_error_signal`` 在 ``Engine.__init__`` 中创建。"""

    _fatal_error_signal: FatalErrorSignal

    async def wait_for_fatal_error(self) -> BaseException:
        """等待引擎 fatal error，供应用主循环触发进程级 self-fence。"""
        return await self._fatal_error_signal.wait()

    def record_fatal_error(self, error: BaseException) -> None:
        """引擎外部（如 lease 续期循环）上报致命错误，触发进程级 self-fence。"""
        self._fatal_error_signal.record(error)


__all__ = ["FatalErrorMixin"]
