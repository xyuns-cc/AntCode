import subprocess
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tests.e2e import helpers, source_repository
from tests.e2e.source_repository import (
    GIT_BASE_URL_ENV,
    GIT_ROOT_ENV,
    E2EGitSource,
    publish_e2e_git_source,
)


def _configure_git_server(monkeypatch, tmp_path, base_url: str) -> None:
    monkeypatch.setenv(GIT_ROOT_ENV, str(tmp_path / "git-root"))
    monkeypatch.setenv(GIT_BASE_URL_ENV, base_url)
    monkeypatch.setattr(source_repository, "require_git_http_service", lambda _base_url: None)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args) -> None:
        return


@contextmanager
def _serve_directory(path):
    handler = partial(_QuietStaticHandler, directory=str(path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_e2e_git_server_configuration_is_required(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(GIT_ROOT_ENV, raising=False)
    monkeypatch.delenv(GIT_BASE_URL_ENV, raising=False)

    with pytest.raises(RuntimeError, match=GIT_ROOT_ENV):
        publish_e2e_git_source("E2E-OK")

    monkeypatch.setenv(GIT_ROOT_ENV, str(tmp_path))
    with pytest.raises(RuntimeError, match=GIT_BASE_URL_ENV):
        publish_e2e_git_source("E2E-OK")


@pytest.mark.parametrize("base_url", ["http://git.example.com/e2e", "https://git.example.com/e2e/"])
def test_publish_e2e_git_source_creates_dumb_http_bare_repo(monkeypatch, tmp_path, base_url: str) -> None:
    _configure_git_server(monkeypatch, tmp_path, base_url)
    log_token = "E2E-OK-dynamic-1234"

    source = publish_e2e_git_source(log_token)
    try:
        assert source.url.startswith(base_url.rstrip("/") + "/e2e-")
        assert source.url.endswith(".git")
        assert source.repository_path.is_dir()
        assert (source.repository_path / "info" / "refs").is_file()
        result = subprocess.run(
            ["git", "--git-dir", str(source.repository_path), "show", "main:spiders/e2e/main.py"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert log_token in result.stdout
    finally:
        source.cleanup()
    assert not source.repository_path.exists()


def test_published_repository_clones_over_dumb_http(monkeypatch, tmp_path) -> None:
    git_root = tmp_path / "git-root"
    git_root.mkdir()
    log_token = "E2E-OK-http-clone"
    with _serve_directory(git_root) as base_url:
        monkeypatch.setenv(GIT_ROOT_ENV, str(git_root))
        monkeypatch.setenv(GIT_BASE_URL_ENV, base_url)
        source = publish_e2e_git_source(log_token)
        clone_path = tmp_path / "clone"
        try:
            subprocess.run(
                ["git", "clone", source.url, str(clone_path)],
                check=True,
                capture_output=True,
            )
            content = (clone_path / source.subdir / source.entry_point).read_text(encoding="utf-8")
            assert log_token in content
        finally:
            source.cleanup()


def test_git_http_preflight_failure_prevents_repository_creation(monkeypatch, tmp_path) -> None:
    git_root = tmp_path / "git-root"
    monkeypatch.setenv(GIT_ROOT_ENV, str(git_root))
    monkeypatch.setenv(GIT_BASE_URL_ENV, "http://127.0.0.1:18081")

    def fail_request(*_args, **_kwargs):
        raise source_repository.URLError("connection refused")

    monkeypatch.setattr(source_repository, "urlopen", fail_request)

    with pytest.raises(RuntimeError, match="Git HTTP 服务不可达"):
        publish_e2e_git_source("E2E-OK")

    assert not git_root.exists()


@pytest.mark.parametrize("base_url", ["file:///tmp/repos", "ssh://git.example.com/repos"])
def test_publish_e2e_git_source_rejects_non_http_base(monkeypatch, tmp_path, base_url: str) -> None:
    _configure_git_server(monkeypatch, tmp_path, base_url)

    with pytest.raises(ValueError, match="http:// 或 https://"):
        publish_e2e_git_source("E2E-OK")


def test_publish_e2e_git_source_rejects_escaping_source_path(monkeypatch, tmp_path) -> None:
    _configure_git_server(monkeypatch, tmp_path, "https://git.example.com/e2e")
    monkeypatch.setenv("ANTCODE_E2E_GIT_SUBDIR", "../outside")

    with pytest.raises(ValueError, match="安全相对路径"):
        publish_e2e_git_source("E2E-OK")


@pytest.mark.asyncio
async def test_create_code_project_uses_published_remote_source(monkeypatch, tmp_path) -> None:
    source = E2EGitSource(
        url="https://git.example.com/e2e/run.git",
        ref="main",
        subdir="examples/job",
        entry_point="job.py",
        expected_log_token="E2E-OK-dynamic",
        repository_path=tmp_path / "run.git",
    )
    calls = []

    async def fake_request_json(_client, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "repo-1"} if path == "/repositories" else {"id": "project-1"}

    monkeypatch.setattr(helpers, "request_json", fake_request_json)
    config = SimpleNamespace(runtime_python_version="3.12", shared_env_name="e2e-env")

    resources = await helpers.create_code_project(
        None,
        "token",
        "worker-1",
        config=config,
        source=source,
    )

    assert resources.project["id"] == "project-1"
    assert resources.repository["id"] == "repo-1"
    repository_json = calls[0][2]["json"]
    assert repository_json["url"] == source.url
    assert repository_json["default_ref"] == "main"
    project_form = calls[1][2]["data"]
    assert project_form["repository_id"] == "repo-1"
    assert project_form["subdir"] == "examples/job"
    assert project_form["code_entry_point"] == "job.py"


@pytest.mark.asyncio
async def test_create_code_project_cleans_repository_when_project_fails(monkeypatch, tmp_path) -> None:
    source = E2EGitSource(
        url="https://git.example.com/e2e/run.git",
        ref="main",
        subdir="examples/job",
        entry_point="job.py",
        expected_log_token="E2E-FAIL",
        repository_path=tmp_path / "run.git",
    )
    calls = []

    async def fake_request_json(_client, method, path, **_kwargs):
        calls.append((method, path))
        if path == "/repositories":
            return {"id": "repo-1"}
        if path == "/projects":
            raise RuntimeError("project create failed")
        return {"success": True, "data": None}

    monkeypatch.setattr(helpers, "request_json", fake_request_json)
    config = SimpleNamespace(runtime_python_version="3.12", shared_env_name="e2e-env")

    with pytest.raises(RuntimeError, match="project create failed"):
        await helpers.create_code_project(
            None,
            "token",
            "worker-1",
            config=config,
            source=source,
        )

    assert calls[-1] == ("DELETE", "/repositories/repo-1")
