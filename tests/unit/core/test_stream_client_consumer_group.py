"""B9 回归：StreamClient 消费者组名必须显式传入，XACK 结果必须校验。

修复前仓库里有两份同名 ``StreamClient``：
- ``infrastructure/redis/streams.py``      DEFAULT_GROUP = "default_workers"
- ``infrastructure/redis/stream_client.py`` DEFAULT_GROUP = "crawl_workers"

调用方漏传 ``group_name`` 时会静默落到不同的默认组，``xack`` 打到不存在
的消费组后 Redis 返回 0，代码不校验，消息永久滞留 PEL。

``streams.py`` 现已整体删除（合并期的纯别名模块也一并清掉），本文件断言
该模块不可再被导入，防止第二份实现以任何形式复活。

本文件用注入的假 Redis 驱动真实的 ``StreamClient`` 与真实调用方（Master
调度事件循环），不 mock 被测类本身。
"""

import importlib.util

import pytest
from antcode_core.common.config import settings
from antcode_core.infrastructure.redis import StreamClient as PackageStreamClient
from antcode_core.infrastructure.redis.stream_client import StreamClient
from antcode_master.control.scheduler_event_loop import SchedulerEventLoop
from loguru import logger

TEST_GROUP = "explicit-test-group"
TEST_STREAM = "antcode:test:stream"
TWO_MESSAGES = 2


class _RecordingRedis:
    """记录 Stream 命令的假 Redis 连接层。

    只替代连接，不替代 ``StreamClient`` —— 被测逻辑仍然是真实实现。
    """

    def __init__(self, *, xack_result: int = 1):
        self._xack_result = xack_result
        self.read_groups: list[str] = []
        self.ack_calls: list[tuple[str, str, tuple]] = []
        self.group_create_calls: list[tuple[str, str]] = []
        self.pending_groups: list[str] = []
        self.autoclaim_groups: list[str] = []
        self.eval_calls: list[tuple] = []

    async def xreadgroup(self, group, _consumer, **_kwargs):
        self.read_groups.append(group)
        return []

    async def xack(self, stream_key, group, *msg_ids):
        self.ack_calls.append((stream_key, group, msg_ids))
        return self._xack_result

    async def xgroup_create(self, stream_key, group, **_kwargs):
        self.group_create_calls.append((stream_key, group))
        return True

    async def xgroup_destroy(self, _stream_key, _group):
        return True

    async def xpending(self, _stream_key, group):
        self.pending_groups.append(group)
        return {"pending": 0, "min": None, "max": None, "consumers": []}

    # redis-py 的 XPENDING/XCLAIM/XAUTOCLAIM 都是位置参数长签名，这里只关心
    # 第二个位置参数（消费者组名），其余用 *args 吞掉。
    async def xpending_range(self, _stream_key, group, *_args, **_kwargs):
        self.pending_groups.append(group)
        return []

    async def xautoclaim(self, _stream_key, group, *_args, **_kwargs):
        self.autoclaim_groups.append(group)
        return ["0-0", [], []]

    async def xclaim(self, _stream_key, _group, *_args, **_kwargs):
        return []

    async def xinfo_groups(self, _stream_key):
        return []

    async def eval(self, _script, numkeys, *args):
        self.eval_calls.append((numkeys, *args))
        return 1


def _client(**kwargs) -> StreamClient:
    return StreamClient(_RecordingRedis(**kwargs))


# ---------------------------------------------------------------------------
# 单一实现 + 无默认组名
# ---------------------------------------------------------------------------


def test_only_one_stream_client_implementation_remains():
    """包级导出必须就是规范实现，legacy ``streams`` 模块必须已经不存在。"""
    assert PackageStreamClient is StreamClient
    assert importlib.util.find_spec("antcode_core.infrastructure.redis.streams") is None


def test_default_consumer_group_constant_is_gone():
    """默认组名是 B9 的根因，任何形式的默认值都不允许再出现。"""
    assert not hasattr(StreamClient, "DEFAULT_GROUP")


# ---------------------------------------------------------------------------
# 漏传组名必须显式失败（而不是走默认值）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("xack", (TEST_STREAM, ["1-0"])),
        ("xack_delete", (TEST_STREAM, ["1-0"])),
        ("xreadgroup", (TEST_STREAM,)),
        ("xreadgroup_typed", (TEST_STREAM,)),
        ("xreadgroup_multi", ([TEST_STREAM],)),
        ("xreadgroup_multi_typed", ([TEST_STREAM],)),
        ("xclaim", (TEST_STREAM, ["1-0"])),
        ("xautoclaim", (TEST_STREAM,)),
        ("xpending", (TEST_STREAM,)),
        ("xpending_range", (TEST_STREAM,)),
        ("xgroup_create", (TEST_STREAM,)),
        ("xgroup_destroy", (TEST_STREAM,)),
        ("ensure_group", (TEST_STREAM,)),
    ],
)
async def test_missing_group_name_fails_loudly(method_name, args):
    method = getattr(_client(), method_name)

    with pytest.raises(TypeError, match="group_name"):
        await method(*args)


# ---------------------------------------------------------------------------
# 组名原样透传给 Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_group_reaches_redis_verbatim():
    redis = _RecordingRedis()
    client = StreamClient(redis)

    await client.ensure_group(TEST_STREAM, TEST_GROUP)
    await client.xreadgroup(TEST_STREAM, TEST_GROUP)
    await client.xautoclaim(TEST_STREAM, TEST_GROUP)
    await client.xpending(TEST_STREAM, TEST_GROUP)
    await client.xack(TEST_STREAM, ["1-0"], TEST_GROUP)

    assert redis.group_create_calls == [(TEST_STREAM, TEST_GROUP)]
    assert redis.read_groups == [TEST_GROUP]
    assert redis.autoclaim_groups == [TEST_GROUP]
    assert redis.pending_groups == [TEST_GROUP]
    assert redis.ack_calls == [(TEST_STREAM, TEST_GROUP, ("1-0",))]


# ---------------------------------------------------------------------------
# 调用方组名防回归：合并前后必须一致
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_event_loop_still_uses_its_own_group():
    """调度事件循环用的是 ``scheduler-events``，不是任何一个历史默认组。"""
    redis = _RecordingRedis()
    loop = SchedulerEventLoop()
    loop._stream_client = StreamClient(redis)

    await loop._stream_client.ensure_group(loop._stream, loop._group)
    await loop._read_messages()
    await loop._ack_messages(["1-0"])

    expected = settings.SCHEDULER_EVENT_GROUP
    assert expected == "scheduler-events"
    assert redis.group_create_calls == [(loop._stream, expected)]
    assert set(redis.read_groups) == {expected}
    assert redis.ack_calls == [(loop._stream, expected, ("1-0",))]


# ---------------------------------------------------------------------------
# XACK 返回值校验（XACK 返回 0 的成因鉴别见 test_stream_ack_zero_causes.py）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_xack_partial_acknowledge_is_logged_as_error():
    client = _client(xack_result=1)
    records: list[str] = []
    sink_id = logger.add(records.append, level="ERROR")

    try:
        acked = await client.xack(TEST_STREAM, ["1-0", "2-0"], TEST_GROUP)
    finally:
        logger.remove(sink_id)

    assert acked == 1
    assert any("XACK 部分确认失败" in record for record in records)


@pytest.mark.asyncio
async def test_xack_full_acknowledge_returns_count():
    client = _client(xack_result=TWO_MESSAGES)

    assert await client.xack(TEST_STREAM, ["1-0", "2-0"], TEST_GROUP) == TWO_MESSAGES


@pytest.mark.asyncio
async def test_xack_empty_ids_is_noop():
    redis = _RecordingRedis(xack_result=0)

    assert await StreamClient(redis).xack(TEST_STREAM, [], TEST_GROUP) == 0
    assert redis.ack_calls == []
