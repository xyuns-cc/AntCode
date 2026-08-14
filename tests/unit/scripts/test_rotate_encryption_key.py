from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import rotate_encryption_key as command

REDISPATCH_DRAIN_CHECK_COMMANDS = 4


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args) -> bool:
        return False


class _ChangingRedis:
    def __init__(self) -> None:
        self.calls = 0

    async def zcard(self, _key: str) -> int:
        self.calls += 1
        return int(self.calls > REDISPATCH_DRAIN_CHECK_COMMANDS)

    async def hlen(self, _key: str) -> int:
        self.calls += 1
        return 0


@dataclass(frozen=True)
class _Workers:
    workers_scanned: int = 1
    hmac_secrets_scanned: int = 1
    redis_passwords_scanned: int = 1
    ciphertexts_requiring_rotation: int = 2
    rows_rewritten: int = 1


@dataclass(frozen=True)
class _Result:
    tables: tuple = ()
    workers: _Workers = _Workers()
    ciphertexts_scanned: int = 2
    ciphertexts_requiring_rotation: int = 2
    rows_rewritten: int = 1


def test_default_mode_is_dry_run() -> None:
    args = command._arguments([])
    command._validate_arguments(args)
    assert not args.apply
    assert not args.verify_primary_only


@pytest.mark.parametrize("mode", ["--apply", "--verify-primary-only"])
def test_writer_stop_confirmation_is_mandatory(mode: str) -> None:
    args = command._arguments([mode])
    with pytest.raises(RuntimeError, match="confirm-writers-stopped"):
        command._validate_arguments(args)


def test_confirmation_allows_offline_dry_run() -> None:
    args = command._arguments(["--confirm-writers-stopped"])
    command._validate_arguments(args)
    assert not args.apply


def test_summary_never_prints_ciphertext_or_plaintext(capsys) -> None:
    args = command._arguments(["--apply", "--confirm-writers-stopped"])
    summary = command._summarize(args, _Result())
    output = capsys.readouterr().out

    assert summary.mode == "apply"
    assert "ciphertexts=2" in output
    assert "gAAAA" not in output


@pytest.mark.asyncio
async def test_close_resources_reports_operation_and_both_close_failures() -> None:
    redis = AsyncMock()
    redis.aclose.side_effect = OSError("redis close failed")
    close_db = AsyncMock(side_effect=RuntimeError("database close failed"))
    operation = ConnectionError("rotation failed")

    with pytest.raises(BaseExceptionGroup) as exc_info:
        await command._close_resources(redis, close_db, operation)

    assert [str(item) for item in exc_info.value.exceptions] == [
        "rotation failed",
        "redis close failed",
        "database close failed",
    ]


@pytest.mark.asyncio
async def test_apply_aborts_if_redispatch_becomes_non_empty_before_commit(monkeypatch) -> None:
    import antcode_core.application.services.security.postgres_encryption_key_rotation as rotation
    import tortoise.transactions as transactions

    monkeypatch.setattr(rotation, "rotate_postgres_ciphertexts", AsyncMock(return_value=_Result()))
    monkeypatch.setattr(transactions, "in_transaction", lambda _name: _Transaction())
    args = command._arguments(["--apply", "--confirm-writers-stopped"])

    with pytest.raises(RuntimeError, match="队列未排空"):
        await command._run(args, _ChangingRedis())
