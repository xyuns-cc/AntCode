from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from scripts import check_ready_streams

URL = "redis://127.0.0.1:6379/0"
NAMESPACE = "antcode"
READY_KEY = "{antcode}:task:ready:worker-a"
GROUP = "antcode-workers"
# 截止时间取极小正值：首轮扫描后 remaining 必然 <= 0，无需真实等待即可复现超时分支。
INSTANT_TIMEOUT_SECONDS = 1e-9
FAST_POLL_SECONDS = 0.0001
GENEROUS_WAIT_SECONDS = 5.0
DRAIN_AFTER_SCANS = 3
DRAINED_ENTRIES = 3
RESIDUE_ENTRIES = 7
RESIDUE_PENDING = 2
RESIDUE_UNCONSUMED = 1
ARGPARSE_ERROR_EXIT_CODE = 2
# 只读门禁绝不能触碰这些命令：前四个会改状态，XRANGE 会翻出加密后的任务负载。
FORBIDDEN_COMMANDS = ("delete", "xdel", "xtrim", "xack", "xrange")


@dataclass
class _FakeGroup:
    name: str = GROUP
    pending: int = 0
    lag: int | None = 0


@dataclass
class _FakeStream:
    entries: int = 0
    groups: list[_FakeGroup] = field(default_factory=list)

    def group(self, name: str) -> _FakeGroup:
        return next(group for group in self.groups if group.name == name)


class _FakeRedis:
    """只实现门禁读路径需要的命令。

    ``FORBIDDEN_COMMANDS`` 里的命令**故意不存在**：脚本一旦调用就是
    AttributeError，只读性由此锁死，无需额外断言。
    """

    def __init__(self) -> None:
        self.types: dict[str, str] = {}
        self.streams: dict[str, _FakeStream] = {}
        self.scan_calls: list[tuple[str, int]] = []
        self.scan_passes = 0
        self.on_scan: Callable[[_FakeRedis], None] | None = None
        self.closed = False

    def add_stream(self, key: str, stream: _FakeStream) -> None:
        self.types[key] = "stream"
        self.streams[key] = stream

    async def ping(self) -> bool:
        return True

    async def scan_iter(self, *, match: str, count: int):
        self.scan_calls.append((match, count))
        self.scan_passes += 1
        if self.on_scan is not None:
            self.on_scan(self)
        prefix = match.removesuffix("*")
        for key in sorted(self.types):
            if key.startswith(prefix):
                yield key.encode()

    async def type(self, key: str) -> bytes:
        return self.types.get(key, "none").encode()

    async def xlen(self, key: str) -> int:
        return self.streams[key].entries

    async def xinfo_groups(self, key: str) -> list[dict[bytes, Any]]:
        return [{b"name": group.name.encode(), b"lag": group.lag} for group in self.streams[key].groups]

    async def xpending(self, key: str, group: str) -> dict[bytes, int]:
        return {b"pending": self.streams[key].group(group).pending}

    async def aclose(self) -> None:
        self.closed = True


def _run_main(client: _FakeRedis, monkeypatch: pytest.MonkeyPatch, *extra: str) -> int:
    monkeypatch.setattr(check_ready_streams, "create_async_redis_client", lambda url, **kwargs: client)
    return check_ready_streams.main(["--url", URL, "--namespace", NAMESPACE, *extra])


def _report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def test_prefix_is_derived_from_the_control_plane_key_builder() -> None:
    assert check_ready_streams.ready_stream_prefix(NAMESPACE) == "{antcode}:task:ready:"


@pytest.mark.asyncio
async def test_discover_matches_ready_queues_and_rejects_derived_keys() -> None:
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream())
    for noise in (
        f"{READY_KEY}:requeue:task-1",
        f"{READY_KEY}:ack:task-1",
        f"{READY_KEY}:{{dlq}}:task:dead_letter",
        "{antcode}:task:result",
        "{other}:task:ready:worker-a",
        "antcode:task:ready:worker-a",
    ):
        client.add_stream(noise, _FakeStream())

    assert await check_ready_streams.discover_ready_streams(client, NAMESPACE) == (READY_KEY,)
    assert client.scan_calls == [("{antcode}:task:ready:*", check_ready_streams.SCAN_COUNT)]


@pytest.mark.parametrize("command", FORBIDDEN_COMMANDS)
def test_gate_never_calls_write_or_payload_commands(command: str) -> None:
    """fake 不实现这些命令；其余用例全绿即证明脚本从未调用它们。"""
    assert not hasattr(_FakeRedis(), command)


def test_acked_streams_exit_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream(entries=DRAINED_ENTRIES, groups=[_FakeGroup()]))

    assert _run_main(client, monkeypatch) == check_ready_streams.EXIT_DRAINED

    report = _report(capsys)
    assert report["drained"] is True
    assert report["blockers"] == []
    assert report["streams"] == [
        {
            "key": READY_KEY,
            "entries": DRAINED_ENTRIES,
            "pending": 0,
            "unconsumed": 0,
            "groups": [{"name": GROUP, "pending": 0, "unconsumed": 0}],
        }
    ]
    assert client.closed is True


def test_pending_messages_exit_with_residue_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    group = _FakeGroup(pending=RESIDUE_PENDING, lag=RESIDUE_UNCONSUMED)
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream(entries=RESIDUE_ENTRIES, groups=[group]))

    assert _run_main(client, monkeypatch) == check_ready_streams.EXIT_RESIDUE

    report = _report(capsys)
    assert report["drained"] is False
    assert report["streams"][0]["pending"] == RESIDUE_PENDING
    assert report["streams"][0]["unconsumed"] == RESIDUE_UNCONSUMED
    assert [blocker["code"] for blocker in report["blockers"]] == ["execution_queue_not_drained"]


def test_stream_without_consumer_group_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream(entries=RESIDUE_ENTRIES))

    assert _run_main(client, monkeypatch) == check_ready_streams.EXIT_RESIDUE
    assert [blocker["code"] for blocker in _report(capsys)["blockers"]] == ["unconsumed_stream"]


def test_missing_lag_is_reported_instead_of_assumed_drained(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream(entries=RESIDUE_ENTRIES, groups=[_FakeGroup(lag=None)]))

    assert _run_main(client, monkeypatch) == check_ready_streams.EXIT_RESIDUE

    report = _report(capsys)
    assert report["streams"][0]["unconsumed"] is None
    assert [blocker["code"] for blocker in report["blockers"]] == ["unknown_stream_lag"]


def test_non_stream_key_is_reported_as_type_blocker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeRedis()
    client.types[READY_KEY] = "list"

    assert _run_main(client, monkeypatch) == check_ready_streams.EXIT_RESIDUE

    report = _report(capsys)
    assert report["streams"] == []
    assert report["blockers"] == [
        {"code": "ready_stream_type", "key": READY_KEY, "detail": "expected=stream, actual=list"}
    ]


def test_wait_returns_once_workers_drain_the_stream(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stream = _FakeStream(entries=RESIDUE_ENTRIES, groups=[_FakeGroup(pending=RESIDUE_PENDING)])
    client = _FakeRedis()
    client.add_stream(READY_KEY, stream)

    def drain(fake: _FakeRedis) -> None:
        if fake.scan_passes > DRAIN_AFTER_SCANS:
            stream.groups[0] = _FakeGroup()

    client.on_scan = drain
    arguments = ("--wait", str(GENEROUS_WAIT_SECONDS), "--poll-interval", str(FAST_POLL_SECONDS))

    assert _run_main(client, monkeypatch, *arguments) == check_ready_streams.EXIT_DRAINED

    report = _report(capsys)
    assert report["attempts"] > 1
    assert report["drained"] is True


def test_wait_timeout_uses_dedicated_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeRedis()
    client.add_stream(READY_KEY, _FakeStream(entries=RESIDUE_ENTRIES, groups=[_FakeGroup(pending=RESIDUE_PENDING)]))

    exit_code = _run_main(client, monkeypatch, "--wait", str(INSTANT_TIMEOUT_SECONDS))

    assert exit_code == check_ready_streams.EXIT_WAIT_TIMEOUT
    report = _report(capsys)
    assert report["attempts"] == 1
    assert report["drained"] is False


@pytest.mark.parametrize("flag", ["--delete", "--drain", "--purge", "--force", "--apply"])
def test_cli_exposes_no_destructive_switch(flag: str) -> None:
    with pytest.raises(SystemExit):
        check_ready_streams._parser().parse_args(["--url", URL, "--namespace", NAMESPACE, flag])


@pytest.mark.parametrize(
    ("request_kwargs", "message"),
    [
        ({"namespace": "bad namespace"}, "字母、数字"),
        ({"namespace": NAMESPACE, "wait_seconds": -1.0}, "--wait"),
        ({"namespace": NAMESPACE, "poll_interval_seconds": 0.0}, "--poll-interval"),
    ],
)
def test_request_validation_rejects_invalid_input(request_kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_ready_streams.ReadyStreamRequest(**request_kwargs).validate()


def test_missing_redis_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        check_ready_streams.main(["--namespace", NAMESPACE])
    assert excinfo.value.code == ARGPARSE_ERROR_EXIT_CODE
