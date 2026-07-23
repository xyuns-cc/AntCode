"""P1-round6 5.2 契约测试:log_batch batch_id 内容确定性防 MULTI/EXEC 重放。

审查文档 round6 5.2:
`Redis MULTI/EXEC 重试可重放 XADD; 有限窗口尾扫不能证明全局幂等`。

现有实现 (log_batch_hash.deterministic_batch_id) 已用内容 sha256 作 batch_id,
event_id = "batch_id:index" 唯一; 服务端唯一索引去重, MULTI/EXEC 重放同
batch payload → 同 event_id → PG 冲突, 不入库。

本契约测试锁死:
1. 相同 (worker_id, entries) → 相同 batch_id (跨调用稳定)
2. 不同 worker_id 或 entries → 不同 batch_id
3. batch_id 长度恒为 64 (sha256 hex)
4. verify_batch_id 只接受 canonical hex
5. 未来加时间戳/随机数会立刻触发第一条测试失败

保护对象:content dedup 的 fingerprint 稳定性, 一旦破坏 (加时间戳 /
NONCE / uuid) 就会让 MULTI/EXEC 重放变成"内容相同 batch_id 不同"
→ 重复入库。
"""

from __future__ import annotations

from types import SimpleNamespace

from antcode_core.common.log_batch_hash import (
    BATCH_ID_HEX_LENGTH,
    deterministic_batch_id,
    is_canonical_batch_id,
    verify_batch_id,
)

_EXPECTED_HEX_LENGTH = 64


class _FakeEntry:
    """Proto-like: SerializeToString(deterministic=True) 返回稳定字节。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def SerializeToString(self, deterministic: bool = False) -> bytes:  # noqa: N802
        assert deterministic is True, "契约要求走 deterministic 编码"
        return self._payload


def test_batch_id_stable_across_invocations():
    entries = [_FakeEntry(b"a"), _FakeEntry(b"b"), _FakeEntry(b"ccc")]
    a = deterministic_batch_id("w-1", entries)
    b = deterministic_batch_id("w-1", entries)
    assert a == b
    assert len(a) == _EXPECTED_HEX_LENGTH
    assert BATCH_ID_HEX_LENGTH == _EXPECTED_HEX_LENGTH


def test_batch_id_changes_when_worker_id_changes():
    entries = [_FakeEntry(b"x")]
    assert deterministic_batch_id("w-1", entries) != deterministic_batch_id("w-2", entries)


def test_batch_id_changes_when_entry_content_changes():
    a = deterministic_batch_id("w-1", [_FakeEntry(b"foo")])
    b = deterministic_batch_id("w-1", [_FakeEntry(b"bar")])
    assert a != b


def test_batch_id_length_prefix_prevents_concat_ambiguity():
    """
    长度前缀应让 [b'ab', b'cd'] 与 [b'abcd'] 产生不同 batch_id。
    没有前缀就会碰撞, 让相邻批次内容"合并"后同 hash 逃过 dedup。
    """
    a = deterministic_batch_id("w-1", [_FakeEntry(b"ab"), _FakeEntry(b"cd")])
    b = deterministic_batch_id("w-1", [_FakeEntry(b"abcd")])
    assert a != b


def test_verify_batch_id_rejects_non_canonical_or_wrong_hash():
    entries = [_FakeEntry(b"x")]
    good = deterministic_batch_id("w-1", entries)
    assert verify_batch_id("w-1", entries, good) is True
    # 声明值必须 canonical hex
    assert verify_batch_id("w-1", entries, "not-hex") is False
    assert verify_batch_id("w-1", entries, "AB" * 32) is False  # 大写
    # canonical 但对内容错的 hash 应被拒
    fake_hash = "0" * 64
    assert verify_batch_id("w-1", entries, fake_hash) is False


def test_empty_entries_still_stable():
    a = deterministic_batch_id("w-1", [])
    b = deterministic_batch_id("w-1", [])
    assert a == b
    assert is_canonical_batch_id(a)


def test_deterministic_flag_must_be_true():
    """
    entry.SerializeToString(deterministic=True) 是契约; 走非 deterministic
    编码会让相同数据在不同版本 Python/proto 里产生不同字节 → batch_id
    分裂 → dedup 失效。_FakeEntry.assert 已锁死这个契约。
    """
    entries = [_FakeEntry(b"probe")]
    # 触发 SerializeToString 断言路径
    _ = deterministic_batch_id("w", entries)


def test_batch_id_stable_under_repeat_call_with_same_type_of_entry_wrapper():
    """跨 SimpleNamespace/自定义类调用, 只要 SerializeToString 字节相同就 stable。"""

    def make_ns(payload: bytes):
        return SimpleNamespace(SerializeToString=lambda deterministic=False: payload)

    a = deterministic_batch_id("w-1", [make_ns(b"z")])
    b = deterministic_batch_id("w-1", [_FakeEntry(b"z")])
    assert a == b
