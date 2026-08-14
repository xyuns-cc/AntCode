"""Master 检查点完整性: 失败路径与"无进度"不得被伪装成有效检查点。

被测对象是生产实际在跑的 ``antcode_master.task_persistence``
（core 侧同名副本是死代码，已删除）。覆盖 B14 P0 回归:
- 损坏的检查点数据不得标成 CHECKPOINTED;
- 从未存过进度的 run 不得合成"已检查点、进度 0"的假检查点;
- 写库失败不得返回成功、不得写缓存;
- 读库失败不得谎报"没有检查点"。
"""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock

import antcode_core.domain.models as models
import antcode_master.task_persistence as tp
import pytest

_RUN_ID = "run-b14-master"
_TASK_ID = 1
_TASK_PUBLIC_ID = "task-1"
_SAVED_PROGRESS = 0.25
_STORED_PROGRESS = 0.42
_EXPECTED_CACHE_WRITES = 1


class _FakeCache:
    """记录读写的缓存替身，用来断言失败时没有落下伪造数据。"""

    def __init__(self, stored=None, read_error=None, delete_error=None):
        self.stored = stored
        self.read_error = read_error
        self.delete_error = delete_error
        self.writes = []
        self.deletes = []

    async def get(self, key):
        if self.read_error:
            raise self.read_error
        return self.stored

    async def set(self, key, value, ttl=None):
        self.writes.append((key, value))
        return True

    async def delete(self, key):
        if self.delete_error:
            raise self.delete_error
        self.deletes.append(key)
        return True


@dataclass
class _Run:
    run_id: str
    task_id: int
    start_time: datetime
    result_data: dict | None = None


@dataclass
class _Task:
    id: int
    public_id: str


def _stored_checkpoint(**overrides) -> dict:
    payload = {
        "run_id": _RUN_ID,
        "task_id": _TASK_ID,
        "task_public_id": _TASK_PUBLIC_ID,
        "progress": _STORED_PROGRESS,
        "checkpoint_data": {"cursor": 7},
    }
    payload.update(overrides)
    return payload


def _install_cache(monkeypatch, cache: _FakeCache) -> _FakeCache:
    monkeypatch.setattr(tp, "unified_cache", cache)
    return cache


def _install_task_run(monkeypatch, task_run) -> None:
    """master 侧 TaskRun 是函数内延迟导入，打桩必须打到源模块上。"""
    monkeypatch.setattr(models, "TaskRun", task_run)


def _service() -> tp.TaskPersistenceService:
    return tp.TaskPersistenceService()


def _checkpoint(**overrides) -> tp.TaskCheckpoint:
    kwargs = {"run_id": _RUN_ID, "task_id": _TASK_ID, "task_public_id": _TASK_PUBLIC_ID}
    kwargs.update(overrides)
    return tp.TaskCheckpoint(**kwargs)


# ------------------------------------------------ 恢复扫描: 进度可信度标记


@pytest.mark.parametrize(
    "corrupted",
    [
        "not-a-mapping",
        {"unexpected_field": 1},
        {"run_id": _RUN_ID, "task_id": _TASK_ID, "task_public_id": _TASK_PUBLIC_ID, "state": "not-a-state"},
        {"run_id": _RUN_ID, "task_id": _TASK_ID, "task_public_id": _TASK_PUBLIC_ID, "started_at": "不是时间"},
    ],
)
def test_corrupted_checkpoint_is_never_reported_as_checkpointed(corrupted):
    run = _Run(_RUN_ID, _TASK_ID, datetime.now(), {"checkpoint": corrupted})

    checkpoint = tp.TaskPersistenceService._checkpoint_for_execution(run, _Task(_TASK_ID, _TASK_PUBLIC_ID))

    assert checkpoint.state is tp.CheckpointState.CORRUPTED
    assert checkpoint.state is not tp.CheckpointState.CHECKPOINTED
    assert checkpoint.progress == 0.0
    assert checkpoint.checkpoint_data == {}
    assert checkpoint.error_message and "损坏" in checkpoint.error_message


@pytest.mark.parametrize("result_data", [None, {}, {"checkpoint": None}])
def test_run_without_stored_checkpoint_is_not_reported_as_checkpointed(result_data):
    """从未存过进度就谎报 CHECKPOINTED，会让非幂等任务被完整重放且无线索。"""
    run = _Run(_RUN_ID, _TASK_ID, datetime.now(), result_data)

    checkpoint = tp.TaskPersistenceService._checkpoint_for_execution(run, _Task(_TASK_ID, _TASK_PUBLIC_ID))

    assert checkpoint.state is tp.CheckpointState.PENDING
    assert checkpoint.state is not tp.CheckpointState.CHECKPOINTED
    assert checkpoint.progress == 0.0
    assert checkpoint.error_message is None


def test_valid_checkpoint_keeps_progress_and_checkpointed_state():
    stored = _stored_checkpoint()
    run = _Run(_RUN_ID, _TASK_ID, datetime.now(), {"checkpoint": stored})

    checkpoint = tp.TaskPersistenceService._checkpoint_for_execution(run, _Task(_TASK_ID, _TASK_PUBLIC_ID))

    assert checkpoint.state is tp.CheckpointState.CHECKPOINTED
    assert checkpoint.progress == _STORED_PROGRESS
    # from_dict 不得改写调用方持有的原始数据
    assert stored["run_id"] == _RUN_ID
    assert "started_at" not in stored


# ------------------------------------------------------------ 写入路径


@pytest.mark.asyncio
async def test_save_checkpoint_does_not_return_true_when_db_write_fails(monkeypatch):
    class _FailingTaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            raise OSError("数据库不可用")

    _install_task_run(monkeypatch, _FailingTaskRun)
    cache = _install_cache(monkeypatch, _FakeCache())

    result = "sentinel"
    with pytest.raises(OSError):
        result = await _service().save_checkpoint(_checkpoint(progress=_SAVED_PROGRESS))

    assert result is not True
    # 落库失败后不得写缓存，否则后续读到的是"看起来已保存"的进度
    assert cache.writes == []


@pytest.mark.asyncio
async def test_save_checkpoint_fails_when_run_row_is_missing(monkeypatch):
    class _EmptyTaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            return None

    _install_task_run(monkeypatch, _EmptyTaskRun)
    cache = _install_cache(monkeypatch, _FakeCache())

    with pytest.raises(tp.CheckpointPersistenceError):
        await _service().save_checkpoint(_checkpoint())

    assert cache.writes == []


@pytest.mark.asyncio
async def test_save_checkpoint_persists_to_db_and_cache_on_success(monkeypatch):
    saved = _Run(_RUN_ID, _TASK_ID, datetime.now(), None)
    saved.save = AsyncMock()

    class _TaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            return saved

    _install_task_run(monkeypatch, _TaskRun)
    cache = _install_cache(monkeypatch, _FakeCache())

    await _service().save_checkpoint(_checkpoint(progress=_SAVED_PROGRESS))

    saved.save.assert_awaited_once()
    assert saved.result_data["checkpoint"]["progress"] == _SAVED_PROGRESS
    assert len(cache.writes) == _EXPECTED_CACHE_WRITES


# ------------------------------------------------------------ 读取路径


@pytest.mark.asyncio
async def test_db_read_failure_is_not_reported_as_missing_checkpoint(monkeypatch):
    class _FailingTaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            raise OSError("数据库不可用")

    _install_task_run(monkeypatch, _FailingTaskRun)
    _install_cache(monkeypatch, _FakeCache())

    with pytest.raises(OSError):
        await _service().get_checkpoint(_RUN_ID)


@pytest.mark.asyncio
async def test_cache_read_failure_falls_back_to_database(monkeypatch):
    """缓存只是加速层，读失败必须回落数据库而不是当作"没有检查点"。"""

    class _TaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            return _Run(_RUN_ID, _TASK_ID, datetime.now(), {"checkpoint": _stored_checkpoint()})

    _install_task_run(monkeypatch, _TaskRun)
    _install_cache(monkeypatch, _FakeCache(read_error=OSError("redis 不可达")))

    checkpoint = await _service().get_checkpoint(_RUN_ID)

    assert checkpoint is not None
    assert checkpoint.progress == _STORED_PROGRESS


@pytest.mark.asyncio
async def test_delete_checkpoint_failure_is_not_swallowed(monkeypatch):
    """删除失败被吞掉，残留进度会被下一轮恢复当成真实进度使用。"""
    _install_cache(monkeypatch, _FakeCache(delete_error=OSError("redis 不可达")))

    with pytest.raises(OSError):
        await _service().delete_checkpoint(_RUN_ID)


@pytest.mark.asyncio
async def test_delete_checkpoint_clears_the_database_copy(monkeypatch):
    """只清缓存不清 DB，会让已判死的 run 被旧进度复活。

    _save_to_db 把进度写进 TaskRun.result_data；get_checkpoint 缓存未命中时
    回落数据库。delete_checkpoint 若不清 DB，_mark_task_failed 之后的下一轮
    恢复仍能读到进度，把终态 run 重新拉起来。
    """
    stored = _Run(_RUN_ID, _TASK_ID, datetime.now(), {"checkpoint": _stored_checkpoint(), "other": 1})
    stored.save = AsyncMock()

    class _TaskRun:
        @staticmethod
        async def get_or_none(**kwargs):
            return stored

    _install_task_run(monkeypatch, _TaskRun)
    cache = _install_cache(monkeypatch, _FakeCache())

    await _service().delete_checkpoint(_RUN_ID)

    assert cache.deletes, "缓存副本未清"
    stored.save.assert_awaited_once()
    assert "checkpoint" not in stored.result_data, "数据库权威副本仍在，run 可被旧进度复活"
    assert stored.result_data["other"] == 1, "不得误删同一 result_data 里的其他字段"
