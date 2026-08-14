"""control 条目 → ControlEvent 构建：畸形 payload 必须走毒消息路径。

``control_event_builders`` 的模块约定是"构建失败一律返回 None，由调用方按
毒消息 ACK 丢弃"。``config_update`` 分支曾把非 dict 的 config 静默改写成
``{}``，于是一条畸形条目变成了一条**看起来完全合法**的空 ConfigUpdate 投给
Worker：毒消息路径被绕过，Worker 拿到一个它无从分辨的空配置更新。
"""

import pytest
from antcode_contracts import control_pb2
from antcode_core.infrastructure.redis import (
    build_config_update_control_payload,
    control_stream,
)
from antcode_gateway.services.control_event_builders import build_control_event

STREAM = control_stream("worker-1")
MSG_ID = "1-0"


def _build(data: dict) -> control_pb2.ControlEvent | None:
    return build_control_event(stream_key=STREAM, msg_id=MSG_ID, data=data)


def test_well_formed_config_update_is_delivered_verbatim() -> None:
    event = _build(build_config_update_control_payload({"max_concurrent_tasks": 8}))

    assert event is not None
    assert event.event_id == f"{STREAM}|{MSG_ID}"
    assert dict(event.config_update.config) == {"max_concurrent_tasks": "8"}


def test_empty_config_update_is_still_a_legitimate_event() -> None:
    """``{}`` 是生产者能合法写出的值（UpdateConfig 传空 config），不是畸形。"""
    event = _build(build_config_update_control_payload({}))

    assert event is not None
    assert dict(event.config_update.config) == {}


@pytest.mark.parametrize(
    "config_field",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty-string"),
        pytest.param("null", id="json-null"),
        pytest.param("[1, 2]", id="json-array"),
        pytest.param('"a string"', id="json-string"),
        pytest.param("8", id="json-number"),
    ],
)
def test_malformed_config_update_is_discarded_as_poison(config_field: str | None) -> None:
    data = {"control_type": "config_update"}
    if config_field is not None:
        data["config"] = config_field

    assert _build(data) is None


def test_unparsable_config_json_is_discarded_as_poison() -> None:
    """decode_stream_payload 对非法 JSON 抛 ValueError，同样按毒消息丢弃。"""
    assert _build({"control_type": "config_update", "config": "{not json"}) is None
