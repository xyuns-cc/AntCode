from types import SimpleNamespace

import pytest
from antcode_core.common.utils.db_optimizer import BulkUpdateOptions, DatabaseOptimizer


class _Query:
    def __init__(self, objects) -> None:
        self._objects = objects

    async def all(self):
        return self._objects


class _Model:
    objects = [SimpleNamespace(id=1, name="old"), SimpleNamespace(id=2, name="old")]
    bulk_calls = []

    @classmethod
    def filter(cls, **_kwargs):
        return _Query(cls.objects)

    @classmethod
    async def bulk_update(cls, objects, fields):
        cls.bulk_calls.append((objects, fields))


@pytest.mark.asyncio
async def test_bulk_update_uses_single_strict_batch_write() -> None:
    _Model.bulk_calls = []

    count = await DatabaseOptimizer.bulk_update(
        _Model,
        [{"id": 1, "name": "new"}, {"id": 2, "name": "newer"}],
        BulkUpdateOptions(batch_size=10),
    )

    assert count == 2
    assert [obj.name for obj in _Model.objects] == ["new", "newer"]
    assert _Model.bulk_calls[0][1] == ["name"]


@pytest.mark.asyncio
async def test_bulk_update_exposes_database_failure() -> None:
    class FailingModel(_Model):
        @classmethod
        async def bulk_update(cls, objects, fields):
            raise RuntimeError("database failed")

    with pytest.raises(RuntimeError, match="database failed"):
        await DatabaseOptimizer.bulk_update(FailingModel, [{"id": 1, "name": "new"}])
