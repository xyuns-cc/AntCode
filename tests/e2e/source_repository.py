"""Publish per-run E2E source repositories over dumb HTTP."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GIT_ROOT_ENV = "ANTCODE_E2E_GIT_ROOT"
GIT_BASE_URL_ENV = "ANTCODE_E2E_GIT_BASE_URL"
_HTTP_GIT_SCHEMES = ("http://", "https://")
GIT_HTTP_TIMEOUT_ENV = "ANTCODE_E2E_GIT_HTTP_TIMEOUT"
DEFAULT_GIT_HTTP_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class E2EGitSource:
    url: str
    ref: str
    subdir: str
    entry_point: str
    expected_log_token: str
    repository_path: Path

    def cleanup(self) -> None:
        if self.repository_path.exists():
            shutil.rmtree(self.repository_path)


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"运行代码 E2E 前必须配置 {name}")
    return value


def _git_root() -> Path:
    root = Path(_required_env(GIT_ROOT_ENV)).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{GIT_ROOT_ENV} 必须是绝对路径")
    return root.resolve()


def _git_base_url() -> str:
    base_url = _required_env(GIT_BASE_URL_ENV).rstrip("/")
    if not base_url.lower().startswith(_HTTP_GIT_SCHEMES):
        raise ValueError(f"{GIT_BASE_URL_ENV} 仅支持 http:// 或 https://")
    return base_url


def require_git_http_service(base_url: str) -> None:
    timeout = float(_env(GIT_HTTP_TIMEOUT_ENV, str(DEFAULT_GIT_HTTP_TIMEOUT_SECONDS)))
    request = Request(f"{base_url}/", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise RuntimeError(f"E2E Git HTTP 服务不可达: {base_url}") from exc
    if status >= 400:
        raise RuntimeError(f"E2E Git HTTP 服务预检失败: url={base_url}, status={status}")


def _safe_relative_path(name: str, value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} 必须是仓库内的安全相对路径")
    return value


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _main_py(log_token: str) -> str:
    return (
        "import logging\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        f"logger.info({log_token!r})\n"
    )


def _create_bare_repository(
    repository_path: Path,
    *,
    log_token: str,
    subdir: str,
    entry_point: str,
    source_code: str | None,
) -> None:
    with TemporaryDirectory(prefix="antcode-e2e-git-") as temp_dir:
        worktree = Path(temp_dir)
        source_dir = worktree / subdir
        source_dir.mkdir(parents=True)
        source_file = source_dir / entry_point
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(source_code or _main_py(log_token), encoding="utf-8")
        _git(worktree, "init", "-b", "main")
        _git(worktree, "config", "user.email", "e2e@example.com")
        _git(worktree, "config", "user.name", "AntCode E2E")
        _git(worktree, "add", subdir)
        _git(worktree, "commit", "-m", "init")
        _git(repository_path.parent, "clone", "--bare", str(worktree), str(repository_path))
    _git(repository_path, "update-server-info")


def publish_e2e_git_source(log_token: str, *, source_code: str | None = None) -> E2EGitSource:
    """Create a unique bare repository consumable through a static HTTP server."""
    if not log_token:
        raise ValueError("E2E log token 不能为空")
    root = _git_root()
    base_url = _git_base_url()
    require_git_http_service(base_url)
    root.mkdir(parents=True, exist_ok=True)
    subdir = _safe_relative_path(
        "ANTCODE_E2E_GIT_SUBDIR",
        _env("ANTCODE_E2E_GIT_SUBDIR", "spiders/e2e"),
    )
    entry_point = _safe_relative_path(
        "ANTCODE_E2E_GIT_ENTRY_POINT",
        _env("ANTCODE_E2E_GIT_ENTRY_POINT", "main.py"),
    )
    repository_name = f"e2e-{uuid.uuid4().hex}.git"
    repository_path = root / repository_name
    try:
        _create_bare_repository(
            repository_path,
            log_token=log_token,
            subdir=subdir,
            entry_point=entry_point,
            source_code=source_code,
        )
    except Exception:
        if repository_path.exists():
            shutil.rmtree(repository_path)
        raise
    return E2EGitSource(
        url=f"{base_url}/{repository_name}",
        ref="main",
        subdir=subdir,
        entry_point=entry_point,
        expected_log_token=log_token,
        repository_path=repository_path,
    )
