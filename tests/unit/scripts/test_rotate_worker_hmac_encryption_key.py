from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace

import antcode_core.application.services.workers.worker_hmac_key_rotation as rotation_module
import pytest
import tortoise.transactions as transaction_module

from scripts import rotate_worker_hmac_encryption_key as command

EXPECTED_VERIFIED_WORKERS = 4


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args) -> bool:
        return False


def test_apply_requires_explicit_writer_shutdown_confirmation() -> None:
    args = command._arguments(["--apply"])

    with pytest.raises(RuntimeError, match="--confirm-writers-stopped"):
        command._validate_arguments(args)


def test_writer_confirmation_is_rejected_without_apply() -> None:
    args = command._arguments(["--confirm-writers-stopped"])

    with pytest.raises(RuntimeError, match="仅可与 --apply"):
        command._validate_arguments(args)


@pytest.mark.asyncio
async def test_dry_run_calls_rotation_without_apply(monkeypatch, capsys) -> None:
    calls: list[bool] = []

    async def rotate(_connection, *, apply: bool):
        calls.append(apply)
        return SimpleNamespace(
            workers_scanned=2,
            hmac_secrets_scanned=2,
            redis_passwords_scanned=1,
            ciphertexts_requiring_rotation=3,
            rows_rewritten=0,
        )

    monkeypatch.setattr(rotation_module, "rotate_worker_credentials", rotate)
    monkeypatch.setattr(transaction_module, "in_transaction", lambda _name: _Transaction())

    await command._run(command._arguments([]))

    assert calls == [False]
    assert "dry-run" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_primary_only_mode_uses_independent_verifier(monkeypatch, capsys) -> None:
    async def verify(_connection):
        return EXPECTED_VERIFIED_WORKERS

    monkeypatch.setattr(rotation_module, "verify_worker_credentials_primary_only", verify)
    monkeypatch.setattr(transaction_module, "in_transaction", lambda _name: _Transaction())

    assert await command._run(command._arguments(["--verify-primary-only"])) == EXPECTED_VERIFIED_WORKERS
    assert f"workers={EXPECTED_VERIFIED_WORKERS}" in capsys.readouterr().out
