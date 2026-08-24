import tarfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.projects import source_bundle_errors as bundle_errors
from antcode_core.application.services.projects import source_bundle_service as module
from antcode_core.application.services.projects.git_transfer_quota import TransferBudget
from antcode_core.application.services.projects.git_transport import GitTransportSession


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

    def fake_clone(repo_dir, source_config, revision, **kwargs):
        auth_config = kwargs["auth_config"]
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
        entry_point="main.py",
        transfer_budget=TransferBudget(1024),
    )

    root_data = Path.cwd() / "data"
    assert root_data in seen["repo_dir"].parents


def test_materialize_bundle_archive_rejection_carries_structured_code(monkeypatch):
    """压缩包线是四条容量线里唯一打包之后才判的一条，也是提交 npm 离线缓存时先触到的那条。

    真机实测（1967 个文件 / 200 MiB 的 npm ``_cacache``）：文件数与解压后总大小都没超，
    tar.gz 后 123.6 MiB 顶穿 100 MiB。这条报错必须能和另外三条区分开。
    """

    def fake_clone(repo_dir, source_config, revision, **kwargs):
        del source_config, revision, kwargs
        (repo_dir / "app").mkdir(parents=True)
        (repo_dir / "app" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(module, "_clone_repo", fake_clone)
    monkeypatch.setattr(module, "MAX_BUNDLE_ARCHIVE_BYTES", 8)

    with pytest.raises(bundle_errors.SourceBundleRejected) as excinfo:
        module._materialize_bundle(
            {"url": "https://example.com/repo.git", "subdir": "app"},
            "a" * 40,
            None,
            entry_point="main.py",
            transfer_budget=TransferBudget(1024),
        )

    assert excinfo.value.error_code == bundle_errors.SOURCE_BUNDLE_ARCHIVE_BYTES_EXCEEDED
    assert bundle_errors.CAPACITY_HINT in excinfo.value.detail


_BRANCH_SHA = "1" * 40
_TAG_SHA = "2" * 40
_PEELED_SHA = "3" * 40


def _patch_ls_remote(monkeypatch, listing: str) -> list[list[str]]:
    monkeypatch.setattr(module, "_resolve_git_endpoint", lambda url: SimpleNamespace(url=url))
    commands: list[list[str]] = []

    def fake_run_git(command, **kwargs):
        del kwargs
        commands.append(command)
        if "--refs" in command:
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout=listing)

    monkeypatch.setattr(module, "_run_git", fake_run_git)
    return commands


def test_resolve_git_revision_prefers_branch_over_same_name_tag(monkeypatch):
    # D1: 同名 branch/tag 并存时 git clone --branch 会解析成 branch，
    # 版本解析必须与之一致，否则检出校验必然失败
    _patch_ls_remote(
        monkeypatch,
        f"{_BRANCH_SHA}\trefs/heads/v1.0\n{_TAG_SHA}\trefs/tags/v1.0\n{_PEELED_SHA}\trefs/tags/v1.0^{{}}\n",
    )
    revision = module._resolve_git_revision(
        {"url": "https://example.com/repo.git", "ref": "v1.0"},
        None,
        transfer_budget=TransferBudget(1024),
    )
    assert revision == _BRANCH_SHA


def test_resolve_git_revision_annotated_tag_uses_peeled_commit(monkeypatch):
    _patch_ls_remote(
        monkeypatch,
        f"{_TAG_SHA}\trefs/tags/v1.0\n{_PEELED_SHA}\trefs/tags/v1.0^{{}}\n",
    )
    revision = module._resolve_git_revision(
        {"url": "https://example.com/repo.git", "ref": "v1.0"},
        None,
        transfer_budget=TransferBudget(1024),
    )
    assert revision == _PEELED_SHA


def test_resolve_git_revision_requests_peeled_pattern_and_handles_lightweight_tag(monkeypatch):
    # ls-remote 的 pattern 是尾部匹配，单查 <ref> 拿不到 ^{} 剥离行，
    # 必须显式请求 <ref>^{}；lightweight tag 无剥离行时直接用 tag sha
    commands = _patch_ls_remote(monkeypatch, f"{_TAG_SHA}\trefs/tags/v1.0\n")
    revision = module._resolve_git_revision(
        {"url": "https://example.com/repo.git", "ref": "v1.0"},
        None,
        transfer_budget=TransferBudget(1024),
    )
    assert revision == _TAG_SHA
    assert commands[-1][-2:] == ["v1.0", "v1.0^{}"]


def test_resolve_git_revision_head_fallback(monkeypatch):
    _patch_ls_remote(monkeypatch, f"{_BRANCH_SHA}\tHEAD\n")
    revision = module._resolve_git_revision(
        {"url": "https://example.com/repo.git"},
        None,
        transfer_budget=TransferBudget(1024),
    )
    assert revision == _BRANCH_SHA


def test_git_http_transport_is_pinned_to_validated_addresses() -> None:
    endpoint = SimpleNamespace(
        scheme="https",
        curl_resolve_value=lambda: "example.com:443:93.184.216.34",
    )

    transport = GitTransportSession(
        safe_config_dir="/tmp/antcode-test-git-config",
        proxy_url="http://127.0.0.1:12345",
    )
    env = module._build_git_env(None, endpoint, transport)

    entries = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert entries["http.followRedirects"] == "false"
    assert entries["http.proxy"] == "http://127.0.0.1:12345"


@pytest.mark.asyncio
async def test_source_bundle_service_writes_postgres_artifact(monkeypatch):
    store = FakeStore()
    budgets = []

    async def fake_auth(url, credential_id):
        return None

    monkeypatch.setattr(module.git_credential_service, "build_auth_config", fake_auth)

    def fake_resolve(source, auth, **kwargs):
        budgets.append(kwargs["transfer_budget"])
        return "b" * 40

    def fake_materialize(source, revision, auth, **kwargs):
        budgets.append(kwargs["transfer_budget"])
        return b"bundle"

    monkeypatch.setattr(module, "_resolve_git_revision", fake_resolve)
    monkeypatch.setattr(
        module,
        "_materialize_bundle",
        fake_materialize,
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
    assert len(budgets) == 2
    assert budgets[0] is budgets[1]
