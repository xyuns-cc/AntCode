from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_web_api.routes.v1 import runs
from fastapi import HTTPException
from fastapi.responses import FileResponse


def _execution():
    artifact = {
        "name": "reports/result.json",
        "uri": "pgartifact://" + "a" * 64,
        "mime_type": "application/json",
    }
    return SimpleNamespace(result_data={"artifacts": [artifact]})


@pytest.mark.asyncio
async def test_artifact_is_fully_verified_before_file_response(monkeypatch, tmp_path) -> None:
    class _Store:
        async def read_blob_to_file(self, content_hash, destination):
            assert content_hash == "a" * 64
            destination.write_bytes(b'{"ok":true}')

    monkeypatch.setattr(runs.scheduler_service, "get_execution_with_permission", AsyncMock(return_value=_execution()))
    monkeypatch.setattr(runs, "PostgresArtifactStore", _Store)
    monkeypatch.setattr(runs, "ensure_runtime_dir", lambda *_parts: tmp_path)

    response = await runs.download_run_artifact(
        "run-1",
        "reports/result.json",
        SimpleNamespace(user_id=7),
    )

    assert isinstance(response, FileResponse)
    assert response.path.read_bytes() == b'{"ok":true}'
    await response.background()
    assert not response.path.exists()


@pytest.mark.asyncio
async def test_corrupt_artifact_never_returns_partial_response(monkeypatch, tmp_path) -> None:
    class _CorruptStore:
        async def read_blob_to_file(self, _content_hash, destination):
            destination.write_bytes(b"partial-corrupt-data")
            raise ValueError("Artifact sha256 不一致")

    monkeypatch.setattr(runs.scheduler_service, "get_execution_with_permission", AsyncMock(return_value=_execution()))
    monkeypatch.setattr(runs, "PostgresArtifactStore", _CorruptStore)
    monkeypatch.setattr(runs, "ensure_runtime_dir", lambda *_parts: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await runs.download_run_artifact(
            "run-1",
            "reports/result.json",
            SimpleNamespace(user_id=7),
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "产物下载失败"
    assert "sha256" not in exc_info.value.detail
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_artifact_hides_storage_details(monkeypatch, tmp_path) -> None:
    class _MissingStore:
        async def read_blob_to_file(self, _content_hash, _destination):
            raise FileNotFoundError("/srv/private/artifacts/secret-path")

    monkeypatch.setattr(runs.scheduler_service, "get_execution_with_permission", AsyncMock(return_value=_execution()))
    monkeypatch.setattr(runs, "PostgresArtifactStore", _MissingStore)
    monkeypatch.setattr(runs, "ensure_runtime_dir", lambda *_parts: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await runs.download_run_artifact(
            "run-1",
            "reports/result.json",
            SimpleNamespace(user_id=7),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "产物不存在"
    assert "secret-path" not in exc_info.value.detail
    assert list(tmp_path.iterdir()) == []
