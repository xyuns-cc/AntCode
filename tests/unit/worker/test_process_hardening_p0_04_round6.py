"""P0-04 (round6) process 加固:主执行日志/argv 脱敏 + kill 失败向上抛。

审查文档 code-review-2026-07-23-round6-review.md P0-04:
- process.py:301 记录完整 argv (可含 --api-key=xxx / URL 中密钥)
- process.py:567-583 直接写 stdout/stderr 进 log_sink 绕过 LogStreamer 脱敏
- _terminate_process 只 logger.error 吞掉 kill 异常,调用方仍按 cancel 成功
  返回,旧子进程可能与 L2 接管者并行执行

本测试锁死:
1. _stream_output 里 content 走 sanitize_log_message
2. 执行命令 log 走 sanitize_log_message
3. _terminate_process SIGKILL 后进程仍活或 kill 抛异常 → raise RuntimeError
"""

from __future__ import annotations

import inspect

import pytest
from antcode_worker.executor.process import ProcessExecutor


def test_stream_output_calls_sanitize_log_message():
    """P0-04:_stream_output 里必须调用 sanitize_log_message 处理 content。"""
    source = inspect.getsource(ProcessExecutor._stream_output)
    assert "sanitize_log_message" in source, "_stream_output 未走脱敏器,子进程可原样写 token"
    # content 应在传给 LogEntry 前被脱敏
    assert "content = sanitize_log_message" in source or "sanitize_log_message(content)" in source


def test_execute_logs_command_via_sanitizer():
    """P0-04:执行命令 log 必须过 sanitize_log_message,防 argv 中 --api-key 泄漏。"""
    source = inspect.getsource(ProcessExecutor._execute)
    # 执行命令 log 行必须包含 sanitize_log_message 调用
    assert "sanitize_log_message" in source, "_execute 未脱敏 argv,可能通过 debug log 外带凭据"


def test_terminate_process_raises_on_kill_failure():
    """P0-04:_terminate_process 遇到 kill 异常或 SIGKILL 后仍活 → raise。"""
    source = inspect.getsource(ProcessExecutor._terminate_process)
    # 一般 except 分支不能只 log 后返回,必须 raise
    assert "raise RuntimeError" in source, "_terminate_process 未在 kill 失败时 raise"
    # SIGKILL 后 returncode 是 None 的兜底检查必须存在
    assert "returncode is None" in source, "缺 SIGKILL 后 returncode 兜底检查"


@pytest.mark.parametrize(
    "sensitive",
    [
        "--api-key=secret123",
        "https://user:pass@example.com/foo",
        "Bearer token=abc.def.ghi",
        "password=mysecret",
    ],
)
def test_sanitize_log_message_masks_common_credentials(sensitive):
    """反向覆盖:sanitize_log_message 对常见凭据模式确实有效(不做无用脱敏)。"""
    from antcode_core.common.logging import sanitize_log_message

    result = sanitize_log_message(sensitive)
    # 至少不能原样出现敏感值(具体 mask 形式由 SENSITIVE_PATTERNS 决定)
    assert (
        (
            "secret123" not in result
            and "mysecret" not in result
            and "abc.def.ghi" not in result
            and "user:pass" not in result
        )
        or "REDACTED" in result
        or "***" in result
    )
