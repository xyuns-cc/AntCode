import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.request import urlopen

from tests.e2e.git_http_server import GitHttpConfig, ThreadingHTTPServer, _handler

IDENTITY = "identity-for-this-round"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _bare_repository(root: Path) -> Path:
    worktree = root / "worktree"
    worktree.mkdir()
    (worktree / "main.py").write_text("print('ok')\n", encoding="utf-8")
    _git(worktree, "init", "-b", "main")
    _git(worktree, "config", "user.email", "e2e@example.com")
    _git(worktree, "config", "user.name", "AntCode E2E")
    _git(worktree, "add", "main.py")
    _git(worktree, "commit", "-m", "init")
    repository = root / "repo.git"
    _git(root, "clone", "--bare", str(worktree), str(repository))
    return repository


def test_smart_git_http_server_supports_shallow_branch_clone(tmp_path) -> None:
    repository = _bare_repository(tmp_path)
    backend = subprocess.check_output(["git", "--exec-path"], text=True).strip()
    config = GitHttpConfig(root=tmp_path, backend=str(Path(backend) / "git-http-backend"), identity=IDENTITY)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    clone_path = tmp_path / "clone"
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            assert response.status == 200
            # 根路径必须自报身份：只回一个定值等于任何占住该端口的进程都能冒充。
            assert json.loads(response.read()) == {
                "identity": IDENTITY,
                "root": str(tmp_path),
                "pid": os.getpid(),
            }
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "main",
                f"http://127.0.0.1:{server.server_port}/{repository.name}",
                str(clone_path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert (clone_path / "main.py").read_text(encoding="utf-8") == "print('ok')\n"
