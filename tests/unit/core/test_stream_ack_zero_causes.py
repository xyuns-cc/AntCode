"""XACK 返回 0 与 XGROUP DESTROY 返回 0：两类"零结果"的成因必须被区分开。

审查发现的两处语义塌陷：

1. ``xack`` 在 XACK 返回 0 时一律抛 ``StreamAckError``。但 0 有两种成因：
   消费者组不存在（真缺陷，消息会永久滞留 PEL），或这批消息已不在该组的
   PEL 里（多副本部署下被别的 consumer XAUTOCLAIM 接管并结算，正常竞态）。
   4 个调用方（result_loop / log_ingest_loop / scheduler_event_loop / poll）
   都不捕获它，把正常竞态一并升格成异常只会让消费循环无谓崩溃退避。
2. ``xgroup_destroy`` 把"Redis 不可达"和"组本来就不存在"压成同一个 False，
   与同文件 ``xgroup_create``（非 BUSYGROUP 一律 raise）语义相反。

本文件用注入的假 Redis 驱动真实的 ``StreamClient``，不 mock 被测类本身。
"""

import pytest
from antcode_core.infrastructure.redis.stream_client import StreamAckError, StreamClient
from loguru import logger
from redis.exceptions import ResponseError

TEST_GROUP = "explicit-test-group"
TEST_STREAM = "antcode:test:stream"

MISSING_KEY_ERROR = (
    "The XGROUP subcommand requires the key to exist. "
    "Note that for CREATE you may want to use the MKSTREAM option to create an empty stream automatically."
)


class _ZeroAckRedis:
    """XACK 恒返回 0；XINFO GROUPS / XGROUP DESTROY 的结果由用例逐个指定。"""

    def __init__(self) -> None:
        self.groups: list[dict] = []
        self.xinfo_groups_error: Exception | None = None
        self.xgroup_destroy_result: int = 1
        self.xgroup_destroy_error: Exception | None = None

    async def xack(self, _stream_key, _group, *_msg_ids):
        return 0

    async def xinfo_groups(self, _stream_key):
        if self.xinfo_groups_error is not None:
            raise self.xinfo_groups_error
        return self.groups

    async def xgroup_destroy(self, _stream_key, _group):
        if self.xgroup_destroy_error is not None:
            raise self.xgroup_destroy_error
        return self.xgroup_destroy_result


def _warnings() -> tuple[list[str], int]:
    records: list[str] = []
    return records, logger.add(records.append, level="WARNING")


# ---------------------------------------------------------------------------
# XACK 返回 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_ack_raises_when_the_consumer_group_does_not_exist():
    """组不存在 ⇒ 消息永远 ACK 不掉，只会被 XAUTOCLAIM 反复重投，必须暴露。"""
    with pytest.raises(StreamAckError, match="XACK 打到不存在的消费者组"):
        await StreamClient(_ZeroAckRedis()).xack(TEST_STREAM, ["1-0", "2-0"], TEST_GROUP)


@pytest.mark.asyncio
async def test_zero_ack_raises_when_the_stream_key_is_gone():
    """Stream 键都不存在 ⇒ 组必然不存在，同样是必须暴露的缺陷。"""
    redis = _ZeroAckRedis()
    redis.xinfo_groups_error = ResponseError("no such key")

    with pytest.raises(StreamAckError, match="XACK 打到不存在的消费者组"):
        await StreamClient(redis).xack(TEST_STREAM, ["1-0"], TEST_GROUP)


@pytest.mark.asyncio
async def test_zero_ack_on_an_existing_group_is_reported_not_raised():
    """组存在 ⇒ 这批消息只是已离开 PEL，本端无事可做，如实返回 0。"""
    redis = _ZeroAckRedis()
    redis.groups = [{b"name": TEST_GROUP.encode()}]
    records, sink_id = _warnings()

    try:
        acked = await StreamClient(redis).xack(TEST_STREAM, ["1-0", "2-0"], TEST_GROUP)
    finally:
        logger.remove(sink_id)

    assert acked == 0
    assert any("已不在该组 PEL" in record for record in records)


@pytest.mark.asyncio
async def test_zero_ack_reads_group_names_decoded_by_the_client():
    """``decode_responses=True`` 的连接返回 str 组名，鉴别不能因此失效。"""
    redis = _ZeroAckRedis()
    redis.groups = [{"name": TEST_GROUP}]
    records, sink_id = _warnings()

    try:
        assert await StreamClient(redis).xack(TEST_STREAM, ["1-0"], TEST_GROUP) == 0
    finally:
        logger.remove(sink_id)

    assert any("已不在该组 PEL" in record for record in records)


@pytest.mark.asyncio
async def test_zero_ack_propagates_unexpected_redis_errors():
    """XINFO GROUPS 因权限/连接失败时不得被降级成"组不存在"。"""
    redis = _ZeroAckRedis()
    redis.xinfo_groups_error = ResponseError("NOPERM this user has no permissions")

    with pytest.raises(ResponseError, match="NOPERM"):
        await StreamClient(redis).xack(TEST_STREAM, ["1-0"], TEST_GROUP)


# ---------------------------------------------------------------------------
# XGROUP DESTROY 返回 0 / 报错
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("destroy_result", "expected"), [(1, True), (0, False)])
async def test_xgroup_destroy_reports_whether_a_group_was_removed(destroy_result, expected):
    redis = _ZeroAckRedis()
    redis.xgroup_destroy_result = destroy_result

    assert await StreamClient(redis).xgroup_destroy(TEST_STREAM, TEST_GROUP) is expected


@pytest.mark.asyncio
async def test_xgroup_destroy_treats_a_missing_stream_key_as_no_group():
    """Stream 键不存在 ⇒ 组必然不存在，等价于"没有组被删除"。"""
    redis = _ZeroAckRedis()
    redis.xgroup_destroy_error = ResponseError(MISSING_KEY_ERROR)

    assert await StreamClient(redis).xgroup_destroy(TEST_STREAM, TEST_GROUP) is False


@pytest.mark.asyncio
async def test_xgroup_destroy_propagates_infrastructure_failures_like_xgroup_create():
    """Redis 不可达绝不能和"组本来就不存在"压成同一个 False。"""
    redis = _ZeroAckRedis()
    redis.xgroup_destroy_error = ConnectionError("Connection closed by server")

    with pytest.raises(ConnectionError, match="Connection closed"):
        await StreamClient(redis).xgroup_destroy(TEST_STREAM, TEST_GROUP)
