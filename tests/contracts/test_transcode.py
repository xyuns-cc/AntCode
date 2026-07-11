"""Tests for ``antcode_contracts.transcode``.

确保所有从 worker/transport 收敛过来的 codec 行为不被回归：
* 13 个 status 字符串别名都映射到正确的 Proto enum
* None / 空串 / 未知值的边界
* Timestamp 双向转换（包含 nano 精度保留）
* ``encode_task_status`` / ``task_status_to_dict`` 与原 worker 端调用形态一致
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

# 与 conftest 一致的 sys.path 注入兜底（独立运行时也能 import）
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _src in (
    _REPO_ROOT / "packages" / "antcode_contracts" / "src",
    _REPO_ROOT / "packages" / "antcode_core" / "src",
    _REPO_ROOT / "services" / "worker" / "src",
):
    s = str(_src)
    if _src.is_dir() and s not in sys.path:
        sys.path.insert(0, s)


from antcode_contracts import common_pb2, data_pb2  # noqa: E402
from antcode_contracts.transcode import (  # noqa: E402
    datetime_to_proto_timestamp,
    encode_task_status,
    log_type_str_to_proto,
    proto_log_type_to_str,
    proto_status_to_str,
    proto_timestamp_to_datetime,
    status_str_to_proto,
    task_status_to_dict,
)


# ---------------------------------------------------------------------------
# Status alias matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "alias,expected",
    [
        ("pending", data_pb2.Status.STATUS_PENDING),
        ("running", data_pb2.Status.STATUS_RUNNING),
        ("success", data_pb2.Status.STATUS_COMPLETED),
        ("completed", data_pb2.Status.STATUS_COMPLETED),
        ("done", data_pb2.Status.STATUS_COMPLETED),
        ("failed", data_pb2.Status.STATUS_FAILED),
        ("failure", data_pb2.Status.STATUS_FAILED),
        ("error", data_pb2.Status.STATUS_FAILED),
        ("cancelled", data_pb2.Status.STATUS_CANCELLED),
        ("canceled", data_pb2.Status.STATUS_CANCELLED),
        ("timeout", data_pb2.Status.STATUS_TIMEOUT),
        ("timed_out", data_pb2.Status.STATUS_TIMEOUT),
        # 大小写不敏感
        ("SUCCESS", data_pb2.Status.STATUS_COMPLETED),
        ("Failed", data_pb2.Status.STATUS_FAILED),
    ],
)
def test_status_str_to_proto_alias_matrix(alias: str, expected: int) -> None:
    assert status_str_to_proto(alias) == expected


def test_status_str_to_proto_none_and_empty() -> None:
    assert status_str_to_proto(None) == data_pb2.Status.STATUS_UNSPECIFIED
    assert status_str_to_proto("") == data_pb2.Status.STATUS_UNSPECIFIED


def test_status_str_to_proto_unknown_falls_back_to_unspecified() -> None:
    assert status_str_to_proto("not_a_real_status") == data_pb2.Status.STATUS_UNSPECIFIED


def test_proto_status_to_str_canonical_mapping() -> None:
    assert proto_status_to_str(data_pb2.Status.STATUS_PENDING) == "pending"
    assert proto_status_to_str(data_pb2.Status.STATUS_RUNNING) == "running"
    assert proto_status_to_str(data_pb2.Status.STATUS_COMPLETED) == "completed"
    assert proto_status_to_str(data_pb2.Status.STATUS_FAILED) == "failed"
    assert proto_status_to_str(data_pb2.Status.STATUS_CANCELLED) == "cancelled"
    assert proto_status_to_str(data_pb2.Status.STATUS_TIMEOUT) == "timeout"
    assert proto_status_to_str(data_pb2.Status.STATUS_UNSPECIFIED) == ""
    # 未知值
    assert proto_status_to_str(999) == ""


# ---------------------------------------------------------------------------
# LogType
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "alias,expected",
    [
        ("stdout", data_pb2.LogType.LOG_TYPE_STDOUT),
        ("stderr", data_pb2.LogType.LOG_TYPE_STDERR),
        ("system", data_pb2.LogType.LOG_TYPE_SYSTEM),
        ("STDOUT", data_pb2.LogType.LOG_TYPE_STDOUT),
    ],
)
def test_log_type_str_to_proto_alias(alias: str, expected: int) -> None:
    assert log_type_str_to_proto(alias) == expected


def test_log_type_str_to_proto_none_and_empty() -> None:
    assert log_type_str_to_proto(None) == data_pb2.LogType.LOG_TYPE_UNSPECIFIED
    assert log_type_str_to_proto("") == data_pb2.LogType.LOG_TYPE_UNSPECIFIED
    assert log_type_str_to_proto("nonsense") == data_pb2.LogType.LOG_TYPE_UNSPECIFIED


def test_proto_log_type_to_str() -> None:
    assert proto_log_type_to_str(data_pb2.LogType.LOG_TYPE_STDOUT) == "stdout"
    assert proto_log_type_to_str(data_pb2.LogType.LOG_TYPE_STDERR) == "stderr"
    assert proto_log_type_to_str(data_pb2.LogType.LOG_TYPE_SYSTEM) == "system"
    assert proto_log_type_to_str(data_pb2.LogType.LOG_TYPE_UNSPECIFIED) == ""
    assert proto_log_type_to_str(999) == ""


# ---------------------------------------------------------------------------
# Timestamp 双向
# ---------------------------------------------------------------------------
def test_datetime_to_proto_timestamp_none() -> None:
    assert datetime_to_proto_timestamp(None) is None


def test_datetime_to_proto_timestamp_round_trip() -> None:
    dt = datetime(2026, 6, 25, 12, 34, 56, 789_000)
    ts = datetime_to_proto_timestamp(dt)
    assert ts is not None
    assert ts.seconds == int(dt.timestamp())
    # microsecond → nanos：789_000 μs == 789_000_000 ns
    assert ts.nanos == 789_000_000

    # 反向
    restored = proto_timestamp_to_datetime(ts)
    assert restored is not None
    # 微秒级精度
    assert restored == dt


def test_proto_timestamp_to_datetime_none_and_zero() -> None:
    assert proto_timestamp_to_datetime(None) is None
    zero = common_pb2.Timestamp(seconds=0, nanos=0)
    assert proto_timestamp_to_datetime(zero) is None


def test_proto_timestamp_to_datetime_partial() -> None:
    # 只 seconds、没 nanos 也算有效
    ts = common_pb2.Timestamp(seconds=1_700_000_000, nanos=0)
    restored = proto_timestamp_to_datetime(ts)
    assert restored is not None
    assert restored == datetime.fromtimestamp(1_700_000_000)


# ---------------------------------------------------------------------------
# encode_task_status / task_status_to_dict
# ---------------------------------------------------------------------------
def test_encode_task_status_minimal() -> None:
    msg = encode_task_status(
        run_id="run-1",
        task_id="task-1",
        worker_id="w1",
        status="success",
    )
    assert msg.run_id == "run-1"
    assert msg.task_id == "task-1"
    assert msg.worker_id == "w1"
    assert msg.status == data_pb2.Status.STATUS_COMPLETED
    assert msg.exit_code == 0
    assert msg.error_message == ""
    assert msg.duration_ms == 0
    # 未传 timestamp → 字段不被 HasField
    assert not msg.HasField("started_at")
    assert not msg.HasField("finished_at")


def test_encode_task_status_full_round_trip() -> None:
    started = datetime(2026, 6, 25, 10, 0, 0)
    finished = datetime(2026, 6, 25, 10, 0, 5)
    msg = encode_task_status(
        run_id="run-x",
        task_id="task-x",
        worker_id="worker-x",
        status="failed",
        exit_code=42,
        error_message="boom",
        started_at=started,
        finished_at=finished,
        duration_ms=5000,
        data={"foo": "bar", "n": 7, "obj": {"k": 1}},
    )

    # 反序列化 — 走 SerializeToString 这条路确保 Proto 数据完整
    raw = msg.SerializeToString()
    restored = data_pb2.TaskStatus()
    restored.ParseFromString(raw)

    info = task_status_to_dict(restored)
    assert info["run_id"] == "run-x"
    assert info["task_id"] == "task-x"
    assert info["worker_id"] == "worker-x"
    assert info["status"] == "failed"
    assert info["exit_code"] == 42
    assert info["error_message"] == "boom"
    assert info["duration_ms"] == 5000
    assert info["started_at"] == started
    assert info["finished_at"] == finished
    # 字符串化的 map<string,string>
    assert info["data"]["foo"] == "bar"
    assert info["data"]["n"] == "7"
    # 非标量值 → json.dumps
    assert info["data"]["obj"] == '{"k": 1}'


def test_encode_task_status_handles_unknown_status_alias() -> None:
    msg = encode_task_status(
        run_id="r",
        task_id="t",
        worker_id="w",
        status="nope",
    )
    assert msg.status == data_pb2.Status.STATUS_UNSPECIFIED


def test_task_status_to_dict_empty_message() -> None:
    msg = data_pb2.TaskStatus()
    info = task_status_to_dict(msg)
    assert info["run_id"] == ""
    assert info["status"] == ""
    assert info["started_at"] is None
    assert info["finished_at"] is None
    assert info["data"] == {}
