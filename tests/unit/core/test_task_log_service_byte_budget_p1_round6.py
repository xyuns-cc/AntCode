"""P1-round6 5.3 回归:日志读取的 UTF-8 字节预算。

审查文档 round6 5.3:
`HTTP 日志允许一次物化 10000 行、单行接近 1 MiB;多次字符串/Unicode 拷贝
可达到数十 GiB 进程内存,配置中的总限制没有统一执行点`。

修复:BoundedLogCollector 按 UTF-8 字节预算收行 (默认 32 MiB), 达到即停止并在
末尾追加 `LOG_TRUNCATED_MARKER`, 不再无护栏物化全部行。它是
read_postgres_execution_logs / read_redis_execution_logs 实际使用的那一个收集器。

本测试锁死:
1. 小体量正常拼接不追加 marker
2. 累加超预算立即截断并追加 marker
3. 单行超预算硬截段
4. 空列表返回空串
5. 多字节字符按字节而非字符计
"""

from __future__ import annotations

from antcode_core.application.services.logs.task_log_readers import (
    LOG_TRUNCATED_MARKER,
    MAX_LOG_READ_BYTES,
    BoundedLogCollector,
)


def _join_bounded(contents: list[str]) -> str:
    """按字节预算喂 collector,返回渲染后的 stdout 文本。"""
    collector = BoundedLogCollector()
    for line in contents:
        if not collector.add("stdout", line):
            break
    return collector.texts()[0]


def test_join_bounded_small_no_marker():
    contents = ["hello", "world", "foo"]
    result = _join_bounded(contents)
    assert result == "hello\nworld\nfoo"
    assert LOG_TRUNCATED_MARKER not in result


def test_join_bounded_empty_returns_empty():
    assert _join_bounded([]) == ""


def test_join_bounded_truncates_on_budget_exhaustion():
    # 构造:10 条每条约 5 MiB, 累加应触发 32 MiB 截断
    big_line = "x" * (5 * 1024 * 1024)  # 5 MiB
    contents = [big_line] * 10
    result = _join_bounded(contents)
    assert LOG_TRUNCATED_MARKER in result
    # 结果字节数应 <= 预算 + marker 长度
    result_bytes = len(result.encode("utf-8"))
    marker_bytes = len(LOG_TRUNCATED_MARKER.encode("utf-8"))
    # +1 是 `\n`.join 在截段和 marker 之间插入的换行
    assert result_bytes <= MAX_LOG_READ_BYTES + marker_bytes + 1


def test_join_bounded_single_oversized_line_hard_truncates():
    # 单行 40 MiB, 超过 32 MiB 预算 → 硬截段 + marker
    big = "a" * (40 * 1024 * 1024)
    result = _join_bounded([big])
    assert LOG_TRUNCATED_MARKER in result
    result_bytes = len(result.encode("utf-8"))
    marker_bytes = len(LOG_TRUNCATED_MARKER.encode("utf-8"))
    # 允许 marker 覆盖误差
    # +1 是 `\n`.join 在截段和 marker 之间插入的换行
    assert result_bytes <= MAX_LOG_READ_BYTES + marker_bytes + 1


def test_join_bounded_multibyte_utf8_uses_byte_length():
    # 中文字符 UTF-8 每字符 3 字节, 验证按字节而非字符计
    line = "汉字" * 100_000  # 200,000 chars, 600,000 bytes
    contents = [line] * 60  # 60 * 600KB = 36 MiB 累加应截断
    result = _join_bounded(contents)
    assert LOG_TRUNCATED_MARKER in result
    result_bytes = len(result.encode("utf-8"))
    marker_bytes = len(LOG_TRUNCATED_MARKER.encode("utf-8"))
    # +1 是 `\n`.join 在截段和 marker 之间插入的换行
    assert result_bytes <= MAX_LOG_READ_BYTES + marker_bytes + 1
