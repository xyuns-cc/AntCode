import tarfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.projects import source_bundle_service as module


class FakeStore:
    def __init__(self):
        self.content = b""

    async def write_blob(self, content, media_type, metadata=None):
        del metadata
        self.content = content
        return SimpleNamespace(
            uri="pgartifact://" + "a" * 64,
            content_hash="a" * 64,
            size_bytes=len(content),
            media_type=media_type,
            artifact_id=123,
        )


def test_source_bundle_tar_excludes_git_directory(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret", encoding="utf-8")
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")

    bundle = module._create_deterministic_tar_gz(root)

    with tarfile.open(fileobj=BytesIO(bundle), mode="r:gz") as tar:
        names = tar.getnames()

    assert names == ["main.py"]


def test_materialize_bundle_uses_root_data_temp_directory(monkeypatch):
    seen: dict[str, Path] = {}

    def fake_clone(repo_dir, source_config, revision, auth_config):
        del source_config, revision, auth_config
        seen["repo_dir"] = repo_dir
        repo_dir.mkdir(parents=True)
        (repo_dir / "spiders" / "news").mkdir(parents=True)
        (repo_dir / "spiders" / "news" / "main.py").write_text(
            "print('ok')\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(module, "_clone_repo", fake_clone)

    module._materialize_bundle(
        {"url": "https://example.com/repo.git", "subdir": "spiders/news"},
        "a" * 40,
        None,
        "main.py",
    )

    root_data = Path.cwd() / "data"
    assert root_data in seen["repo_dir"].parents


@pytest.mark.asyncio
async def test_source_bundle_service_writes_postgres_artifact(monkeypatch):
    store = FakeStore()

    async def fake_auth(url, credential_id):
        return None

    monkeypatch.setattr(module.git_credential_service, "build_auth_config", fake_auth)
    monkeypatch.setattr(module, "_resolve_git_revision", lambda source, auth: "b" * 40)
    monkeypatch.setattr(
        module,
        "_materialize_bundle",
        lambda source, revision, auth, entry_point: b"bundle",
    )

    service = module.SourceBundleService(store)
    bundle = await service.create_git_source_bundle(
        project_public_id="proj-1",
        source_config={"url": "https://example.com/repo.git"},
        entry_point="main.py",
    )

    assert store.content == b"bundle"
    assert bundle.uri == "pgartifact://" + "a" * 64
    assert bundle.entry_point == "main.py"
    assert bundle.resolved_revision == "b" * 40
