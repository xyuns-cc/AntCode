"""[3/7] 起 E2E Git 源这一步的判据必须能区分"我起的"和"别人占着端口"。

原状：`nohup … &` 加一句 `curl` 探活。端口被上一轮遗留的孤儿占着时，新进程
EADDRINUSE 秒死、curl 却对着孤儿一次就成功，整轮 E2E 因此对着别人的仓库树跑还
报全过（测试机已复现）。下面每条断言都配一正一反两臂，"全过"不能来自"根本没起"。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from scripts import start_e2e_git_source

ROOT = Path(__file__).resolve().parents[3]
SHELL_SCRIPT = ROOT / "infra/docker/run-gateway-e2e.sh"
LAUNCH_TIMEOUT_SECONDS = 60
PROBE_TIMEOUT_SECONDS = 5


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run_launcher(root: Path, base_url: str, state: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "scripts.start_e2e_git_source",
        "--root",
        str(root),
        "--base-url",
        base_url,
        "--log",
        str(state / "git-http.log"),
        "--pid-file",
        str(state / "git-http.pid"),
    ]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=LAUNCH_TIMEOUT_SECONDS
    )


def _terminate(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    for _ in range(int(PROBE_TIMEOUT_SECONDS / 0.1)):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)


def test_launcher_starts_the_source_and_the_recorded_pid_is_the_process_that_answers(tmp_path: Path) -> None:
    """必须成功臂：端口空着就要正常跑通，否则"能挡住占用"只是把正常路径也挡死了。"""
    port = _free_port()
    state = tmp_path / "state"
    state.mkdir()

    result = _run_launcher(tmp_path / "git", f"http://127.0.0.1:{port}", state)

    assert result.returncode == 0, result.stderr
    pid = int((state / "git-http.pid").read_text(encoding="utf-8").strip())
    try:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=PROBE_TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read())
        # 记下的 PID 就是真正在 listen 的那个进程，teardown 的 kill 才打得中。
        assert answer["pid"] == pid
        assert answer["root"] == str((tmp_path / "git").resolve())
        assert len(answer["identity"]) == start_e2e_git_source.IDENTITY_BYTES * 2
    finally:
        _terminate(pid)


def test_launcher_refuses_an_occupied_port_and_names_the_occupant(tmp_path: Path) -> None:
    """必须失败臂：端口被别人占着要当场失败并报出占用者，而不是继续往下跑。"""
    state = tmp_path / "state"
    state.mkdir()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupant:
        occupant.bind(("127.0.0.1", 0))
        occupant.listen(1)
        port = int(occupant.getsockname()[1])

        result = _run_launcher(tmp_path / "git", f"http://127.0.0.1:{port}", state)

    assert result.returncode != 0
    assert f"端口 {port} 已被占用" in result.stderr
    assert not (state / "git-http.pid").exists()


def test_launcher_surfaces_the_child_exit_code_instead_of_reporting_success(tmp_path: Path) -> None:
    """必须失败臂：进程起不来就必须失败——探活成功从来不等于"我起来了"。"""
    state = tmp_path / "state"
    state.mkdir()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    result = _run_launcher(blocked / "git", f"http://127.0.0.1:{_free_port()}", state)

    assert result.returncode != 0
    assert "E2E Git 源进程已退出（exit=" in result.stderr
    # 子进程自己的诊断必须被带出来，否则失败原因只留在没人读的日志文件里。
    assert "日志尾部: Traceback" in result.stderr
    assert not (state / "git-http.pid").exists()


def test_launcher_rejects_a_base_url_without_an_explicit_port() -> None:
    with pytest.raises(ValueError, match="必须显式带端口"):
        start_e2e_git_source.service_port("http://192.0.2.10")


def test_release_e2e_script_delegates_step_three_to_the_guarded_launcher() -> None:
    """脚本层没有可执行的门禁，这里静态钉住 [3/7] 不许退回"起了就算成功"。"""
    lines = SHELL_SCRIPT.read_text(encoding="utf-8").splitlines()
    # 只看可执行语句：注释里写着这段历史，不能让它自己把断言喂饱。
    statements = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    assert "python -m scripts.start_e2e_git_source" in statements
    assert "nohup" not in statements
    assert "curl" not in statements
    # 端口只在 scripts/release_e2e_environment.py 定义一份，脚本不许再抄一个字面量。
    assert "18081" not in statements
