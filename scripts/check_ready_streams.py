"""Ready stream 排空门禁：只读扫描新旧两种 ready stream key 形态。

本次升级把 ready stream 的 Redis key 从 ``antcode:task:ready:<worker>`` 改成带
Cluster hash-tag 的 ``{antcode}:task:ready:<worker>``（权威构造见
``antcode_core.infrastructure.redis.control_plane.task_ready_stream``）。升级与
回滚各会在另一形态上留下一批孤儿消息：不投递、不进 DLQ、无日志，只能等
master reconcile 超时后标 FAILED。本脚本在停机窗口内做门禁，确认两种形态都
已排空。

用法::

    python -m scripts.check_ready_streams                 # 单次只读检查
    python -m scripts.check_ready_streams --wait 300      # 轮询直到排空或超时

退出码：``0`` 已排空；``1`` 存在残留（未启用 ``--wait``）；``2`` ``--wait``
超时仍有残留。非零码即为部署脚本的阻断信号。

本脚本**刻意不提供任何删除/丢弃开关**。ready stream 中的残留是尚未执行的真实
任务，自动清理等于静默丢任务。脚本只报告事实，处置由人决策。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, cast

from antcode_core.infrastructure.redis.control_plane import task_ready_stream
from antcode_core.infrastructure.redis.factory import create_async_redis_client

from scripts.crawl_redis_upgrade_contract import Blocker, StreamGroupStats, StreamStats
from scripts.crawl_redis_upgrade_execution import inspect_stream

EXIT_DRAINED = 0
EXIT_RESIDUE = 1
EXIT_WAIT_TIMEOUT = 2

SCAN_COUNT = 500
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
CURRENT_SHAPE = "current"
LEGACY_SHAPE = "legacy"

_WORKER_SEGMENT = r"[A-Za-z0-9_-]+"
_NAMESPACE_PATTERN = re.compile(_WORKER_SEGMENT)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


@dataclass(frozen=True)
class ReadyStreamRequest:
    namespace: str
    wait_seconds: float = 0.0
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS

    def validate(self) -> None:
        if not _NAMESPACE_PATTERN.fullmatch(self.namespace):
            raise ValueError("Redis namespace 只能包含字母、数字、下划线和连字符")
        if self.wait_seconds < 0:
            raise ValueError("--wait 不能为负数")
        if self.poll_interval_seconds <= 0:
            raise ValueError("--poll-interval 必须为正数")


@dataclass(frozen=True)
class ReadyStreamStats:
    key: str
    shape: str
    entries: int
    pending: int
    unconsumed: int | None
    groups: tuple[StreamGroupStats, ...]

    @classmethod
    def from_stream(cls, shape: str, stats: StreamStats) -> ReadyStreamStats:
        lags = [group.unconsumed for group in stats.groups]
        unconsumed = None if None in lags else sum(lag for lag in lags if lag is not None)
        pending = sum(group.pending for group in stats.groups)
        return cls(stats.key, shape, stats.entries, pending, unconsumed, stats.groups)


@dataclass(frozen=True)
class ReadyStreamReport:
    namespace: str
    attempts: int
    wait_seconds: float
    streams: tuple[ReadyStreamStats, ...]
    blockers: tuple[Blocker, ...]

    @property
    def drained(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["drained"] = self.drained
        return value


def stream_prefixes(namespace: str) -> tuple[tuple[str, str], ...]:
    """返回 ``(形态, key 前缀)``；当前形态从权威 key 构造函数派生。

    传入空 worker_id 拿到的就是 ``{ns}:task:ready:`` 前缀，因此 control_plane
    改了 key 形状这里会自动跟随。legacy 形态定义为当前形态去掉 Cluster
    hash-tag 大括号——找不到大括号说明迁移前提已不成立，直接报错而非静默跳过。
    """
    current = task_ready_stream("", namespace=namespace)
    tagged = f"{{{namespace}}}"
    if tagged not in current:
        raise RuntimeError(f"ready stream key 已不含 Cluster hash-tag，无法派生 legacy 形态: {current}")
    return ((CURRENT_SHAPE, current), (LEGACY_SHAPE, current.replace(tagged, namespace, 1)))


async def discover_ready_streams(client: Any, namespace: str) -> tuple[tuple[str, str], ...]:
    """SCAN 两种形态的 ready stream key。

    ``:requeue:`` / ``:ack:`` / ``:{dlq}:`` 等派生 key 也会被前缀 glob 命中，
    靠 worker 段的完整匹配剔除——它们不是 ready 队列本体。
    """
    found: dict[str, str] = {}
    for shape, prefix in stream_prefixes(namespace):
        pattern = re.compile(rf"^{re.escape(prefix)}{_WORKER_SEGMENT}$")
        async for raw_key in client.scan_iter(match=f"{prefix}*", count=SCAN_COUNT):
            key = _text(raw_key)
            if pattern.fullmatch(key):
                found[key] = shape
    return tuple(sorted((shape, key) for key, shape in found.items()))


async def _inspect_ready_stream(client: Any, shape: str, key: str) -> tuple[ReadyStreamStats | None, list[Blocker]]:
    redis_type = _text(await client.type(key))
    if redis_type != "stream":
        return None, [Blocker("ready_stream_type", key, f"expected=stream, actual={redis_type}")]
    stats, findings = await inspect_stream(client, key, inspect_envelopes=False)
    return ReadyStreamStats.from_stream(shape, stats), findings


async def scan_once(client: Any, request: ReadyStreamRequest, attempt: int) -> ReadyStreamReport:
    streams: list[ReadyStreamStats] = []
    blockers: list[Blocker] = []
    for shape, key in await discover_ready_streams(client, request.namespace):
        stats, findings = await _inspect_ready_stream(client, shape, key)
        if stats is not None:
            streams.append(stats)
        blockers.extend(findings)
    return ReadyStreamReport(
        namespace=request.namespace,
        attempts=attempt,
        wait_seconds=request.wait_seconds,
        streams=tuple(streams),
        blockers=tuple(blockers),
    )


async def wait_until_drained(client: Any, request: ReadyStreamRequest) -> ReadyStreamReport:
    """轮询到排空或到达 ``--wait`` 截止时间；``--wait 0`` 即单次检查。"""
    deadline = time.monotonic() + request.wait_seconds
    attempt = 0
    while True:
        attempt += 1
        report = await scan_once(client, request, attempt)
        remaining = deadline - time.monotonic()
        if report.drained or remaining <= 0:
            return report
        await asyncio.sleep(min(request.poll_interval_seconds, remaining))


async def _close_client(client: Any, failure: BaseException | None) -> None:
    try:
        await client.aclose()
    except BaseException as close_failure:
        if failure is not None:
            raise BaseExceptionGroup(
                "ready stream 检查与 Redis 关闭均失败",
                [failure, close_failure],
            ) from failure
        raise


async def run(url: str, request: ReadyStreamRequest) -> ReadyStreamReport:
    request.validate()
    client = create_async_redis_client(url, decode_responses=False)
    failure: BaseException | None = None
    try:
        if not await cast(Awaitable[bool], client.ping()):
            raise RuntimeError("Redis PING 未返回成功")
        return await wait_until_drained(client, request)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        await _close_client(client, failure)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ready stream 排空门禁（只读；无删除开关）",
        epilog=f"退出码：{EXIT_DRAINED}=已排空，{EXIT_RESIDUE}=存在残留，{EXIT_WAIT_TIMEOUT}=--wait 超时仍有残留",
    )
    parser.add_argument("--url", help="目标 Redis URL；默认读取 REDIS_URL")
    parser.add_argument("--namespace", help="目标 namespace；默认读取 REDIS_NAMESPACE")
    parser.add_argument(
        "--wait",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="轮询直到排空；超时仍有残留则以非零码失败（默认 0，单次检查）",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=f"--wait 的轮询间隔秒数（默认 {DEFAULT_POLL_INTERVAL_SECONDS}）",
    )
    return parser


def _required_setting(value: str | None, environment_name: str) -> str:
    candidate = os.environ.get(environment_name, "") if value is None else value
    resolved = candidate.strip()
    if not resolved:
        raise ValueError(f"必须通过参数或 {environment_name} 提供配置")
    return resolved


def _exit_code(report: ReadyStreamReport) -> int:
    if report.drained:
        return EXIT_DRAINED
    return EXIT_WAIT_TIMEOUT if report.wait_seconds > 0 else EXIT_RESIDUE


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        url = _required_setting(args.url, "REDIS_URL")
        namespace = _required_setting(args.namespace, "REDIS_NAMESPACE")
    except ValueError as exc:
        parser.error(str(exc))
    request = ReadyStreamRequest(
        namespace=namespace,
        wait_seconds=float(args.wait),
        poll_interval_seconds=float(args.poll_interval),
    )
    report = asyncio.run(run(url, request))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_DRAINED",
    "EXIT_RESIDUE",
    "EXIT_WAIT_TIMEOUT",
    "ReadyStreamReport",
    "ReadyStreamRequest",
    "ReadyStreamStats",
    "discover_ready_streams",
    "main",
    "run",
    "scan_once",
    "stream_prefixes",
    "wait_until_drained",
]
