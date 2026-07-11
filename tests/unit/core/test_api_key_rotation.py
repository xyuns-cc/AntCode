"""API Key 双 key grace 期轮换 + 验证逻辑。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.common.security import api_key as api_key_mod
from antcode_core.domain.models.worker import Worker


def _fake_worker(api_key=None, prev=None, prev_expires=None, public_id="w-1"):
    return SimpleNamespace(
        public_id=public_id,
        api_key_hash=api_key_mod.hash_api_key(api_key) if api_key else None,
        api_key_previous_hash=api_key_mod.hash_api_key(prev) if prev else None,
        api_key_previous_expires_at=prev_expires,
        save=AsyncMock(),
    )


def _query(exists: bool):
    query = MagicMock()
    query.filter.return_value = query
    query.exists = AsyncMock(return_value=exists)
    return query


@pytest.mark.asyncio
async def test_verify_accepts_current_api_key(monkeypatch):
    current_query = _query(True)
    filter_mock = MagicMock(return_value=current_query)
    monkeypatch.setattr(Worker, "filter", filter_mock)

    assert await api_key_mod.verify_api_key("ak_current", worker_id="w-1") is True
    filter_mock.assert_called_once_with(api_key_hash=api_key_mod.hash_api_key("ak_current"))


@pytest.mark.asyncio
async def test_verify_accepts_previous_within_grace(monkeypatch):
    current_query = _query(False)
    previous_query = _query(True)
    filter_mock = MagicMock(side_effect=[current_query, previous_query])
    monkeypatch.setattr(Worker, "filter", filter_mock)

    assert await api_key_mod.verify_api_key("ak_old", worker_id="w-1") is True
    previous_filters = filter_mock.call_args_list[1].kwargs
    assert previous_filters["api_key_previous_hash"] == api_key_mod.hash_api_key("ak_old")
    assert previous_filters["api_key_previous_expires_at__gt"].tzinfo is not None


@pytest.mark.asyncio
async def test_verify_rejects_expired_previous(monkeypatch):
    monkeypatch.setattr(Worker, "filter", MagicMock(side_effect=[_query(False), _query(False)]))

    assert await api_key_mod.verify_api_key("ak_old", worker_id="w-1") is False


@pytest.mark.asyncio
async def test_rotate_moves_old_key_to_previous(monkeypatch):
    worker = _fake_worker(api_key="ak_old_value")
    old_hash = worker.api_key_hash
    monkeypatch.setattr(Worker, "get_or_none", AsyncMock(return_value=worker))

    result = await api_key_mod.rotate_worker_api_key("w-1", grace_minutes=30)

    assert result["api_key"].startswith("ak_")
    assert worker.api_key_previous_hash == old_hash
    assert worker.api_key_hash == api_key_mod.hash_api_key(result["api_key"])
    assert not hasattr(worker, "api_key")
    delta = worker.api_key_previous_expires_at - datetime.now(UTC)
    assert timedelta(minutes=29) <= delta <= timedelta(minutes=31)
    worker.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_clears_previous(monkeypatch):
    worker = _fake_worker(
        api_key="ak_new",
        prev="ak_old",
        prev_expires=datetime.now(UTC) + timedelta(minutes=10),
    )
    monkeypatch.setattr(Worker, "get_or_none", AsyncMock(return_value=worker))

    await api_key_mod.finalize_worker_api_key_rotation("w-1")

    assert worker.api_key_previous_hash is None
    assert worker.api_key_previous_expires_at is None
    worker.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_rotate_unknown_worker_raises(monkeypatch):
    monkeypatch.setattr(Worker, "get_or_none", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="Worker 不存在"):
        await api_key_mod.rotate_worker_api_key("missing")
