"""P1-SEC-01: Worker 日志脱敏必须 fail-closed。

此前 ``antcode_worker.logs.streamer`` 捕获脱敏模块导入异常后原样返回日志，
token/密码可明文进入 ingest stream 与 PG。修复后：

1. 正常路径：stdout/stderr 与系统日志都必须经过核心 ``sanitize_log_message``；
2. 失败路径：脱敏模块导入失败时模块导入直接抛 ImportError，
   进程在启动阶段拒绝运行，任何日志行都不会以原文形式发出。
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest

STREAMER_MODULE = "antcode_worker.logs.streamer"
SANITIZER_MODULE = "antcode_core.common.logging"

SECRET_LINE = 'api_key="sk-super-secret-value-123456"'


class _CollectSink:
    def __init__(self) -> None:
        self.entries = []

    async def write(self, entry) -> bool:
        self.entries.append(entry)
        return True


def test_streamer_uses_core_sanitizer_without_fallback() -> None:
    """模块级绑定必须就是核心脱敏函数本体，不存在本地兜底实现。"""
    streamer = importlib.import_module(STREAMER_MODULE)
    core_logging = importlib.import_module(SANITIZER_MODULE)

    assert streamer.sanitize_log_message is core_logging.sanitize_log_message


@pytest.mark.asyncio
async def test_process_line_redacts_secrets() -> None:
    from antcode_worker.domain.enums import LogStream
    from antcode_worker.logs.streamer import LogStreamer

    sink = _CollectSink()
    streamer = LogStreamer(run_id="run-redact", sinks=[sink])

    await streamer._process_line(SECRET_LINE, LogStream.STDOUT)

    assert len(sink.entries) == 1
    assert "sk-super-secret-value-123456" not in sink.entries[0].content
    assert "REDACTED" in sink.entries[0].content


@pytest.mark.asyncio
async def test_write_system_log_redacts_secrets() -> None:
    from antcode_worker.logs.streamer import LogStreamer

    sink = _CollectSink()
    streamer = LogStreamer(run_id="run-redact-sys", sinks=[sink])

    await streamer.write_system_log(SECRET_LINE)

    assert len(sink.entries) == 1
    assert "sk-super-secret-value-123456" not in sink.entries[0].content


def test_streamer_import_fails_closed_when_sanitizer_unavailable(monkeypatch) -> None:
    """模拟脱敏模块损坏：streamer 导入必须失败（fail-closed），不得静默回退。"""
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == SANITIZER_MODULE:
            raise ImportError("simulated redaction module breakage")
        return real_import(name, *args, **kwargs)

    saved_modules = {name: sys.modules.pop(name) for name in (STREAMER_MODULE, SANITIZER_MODULE) if name in sys.modules}
    try:
        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        with pytest.raises(ImportError, match="simulated redaction module breakage"):
            importlib.import_module(STREAMER_MODULE)
        # 导入失败后模块不得残留在 sys.modules（不存在半初始化的 streamer）
        assert STREAMER_MODULE not in sys.modules
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
        sys.modules.update(saved_modules)
        importlib.import_module(STREAMER_MODULE)


def test_unsanitized_iter_stream_helper_removed() -> None:
    """旧的 ``iter_stream`` 产出未脱敏 LogEntry 且无调用方，必须保持删除状态。"""
    streamer = importlib.import_module(STREAMER_MODULE)

    assert not hasattr(streamer, "iter_stream")
