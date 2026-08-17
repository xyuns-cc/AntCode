"""Stream 字段解码不得把「预期的非 JSON 值」当成故障来记 ERROR。

Stream 字段天然双模：结构化字段写成 JSON，标量字段原样写入。旧实现对每个字段直接调
``from_json``，而 ``from_json`` 在抛异常前无条件 ``logger.error``——于是这条被注释明确
文档化的预期分支每次任务下发都打 2~4 条 ERROR（``worker-ui-001`` / ISO 时间戳 / 空串 /
``True`` 全部命中）。噪声随任务吞吐线性增长，真实反序列化故障被淹没，基于 ERROR 的告警
持续误报。

这里同时钉住反面：真正「必须是 JSON」的地方解析失败，仍然要记 ERROR 并抛异常。
"""

from __future__ import annotations

import pytest
from antcode_core.common.exceptions import SerializationError
from antcode_core.common.serialization import from_json, try_from_json
from antcode_core.infrastructure.redis.stream_codec import JsonCodec
from antcode_core.infrastructure.redis.stream_records import decode_message_data
from loguru import logger

# 容器内实测会打 ERROR 的真实字段值：只有 '1' 是合法 JSON。
SCALAR_FIELDS = {
    b"worker_id": b"worker-ui-001",
    b"created_at": b"2026-08-17T18:33:45+00:00",
    b"is_active": b"True",
    b"note": b"",
}
JSON_FIELDS = {b"retry": b"1", b"payload": b'{"task_id": 7}'}


@pytest.fixture
def error_logs() -> list[str]:
    captured: list[str] = []
    sink_id = logger.add(
        lambda message: captured.append(message.record["message"]),
        level="ERROR",
        format="{message}",
    )
    yield captured
    logger.remove(sink_id)


def test_stream_message_decode_keeps_scalar_fields_without_logging_errors(error_logs: list[str]) -> None:
    decoded = decode_message_data({**SCALAR_FIELDS, **JSON_FIELDS})

    assert decoded == {
        "worker_id": "worker-ui-001",
        "created_at": "2026-08-17T18:33:45+00:00",
        "is_active": "True",
        "note": "",
        "retry": 1,
        "payload": {"task_id": 7},
    }
    assert error_logs == []


def test_json_codec_decode_keeps_scalar_fields_without_logging_errors(error_logs: list[str]) -> None:
    decoded = JsonCodec().decode({**SCALAR_FIELDS, **JSON_FIELDS})

    assert decoded["worker_id"] == "worker-ui-001"
    assert decoded["note"] == ""
    assert decoded["payload"] == {"task_id": 7}
    assert error_logs == []


def test_try_from_json_reports_a_miss_instead_of_raising(error_logs: list[str]) -> None:
    assert try_from_json("worker-ui-001") == (False, None)
    assert try_from_json('{"task_id": 7}') == (True, {"task_id": 7})
    assert error_logs == []


def test_real_deserialization_failures_still_raise_and_log(error_logs: list[str]) -> None:
    """反面：``from_json`` 用于"这里必须是 JSON"的场景，故障必须依旧可见。"""
    with pytest.raises(SerializationError):
        from_json("{not json")

    assert len(error_logs) == 1
    assert "JSON 反序列化失败" in error_logs[0]
