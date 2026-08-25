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


def _exclusive_bind_fails(port: int) -> bool:
    """无 SO_REUSEADDR 的独占绑定绑不上——孤立 TIME_WAIT 与真占用都会让它失败。

    只当"这个端口上确实还有 TIME_WAIT 残留"的证据用：没有它，TIME_WAIT 那条测试
    在一个早已干净的端口上也会绿，跟修没修完全无关。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return True
        return False


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
    """必须失败臂：端口被别人占着要当场失败并报出占用者，而不是继续往下跑。

    占用者刻意绑 127.0.0.1 而不是通配地址：macOS 上带 SO_REUSEADDR 的 0.0.0.0 绑定
    能骑在这种 LISTEN 之上并成功（已实测），所以这条同时钉死"守卫只用 SO_REUSEADDR
    绑一次"那种改法——那样改，守卫在开发机上对这种占用完全失效。
    """
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


def test_launcher_starts_when_the_port_only_holds_a_time_wait_remnant(tmp_path: Path) -> None:
    """必须成功臂：刚杀掉上一轮的 Git 源、端口上只剩 TIME_WAIT，这一轮要能立刻起来。

    这正是发布 e2e 的常态顺序：上一轮起过、探活过（HTTP 带 Connection: close，服务端
    先关，于是 TIME_WAIT 落在本端口上）、teardown 把它 kill 掉，紧接着跑下一轮。
    Git 源自己 allow_reuse_address=1，这种端口它绑得上；守卫要是比它还严，运维会卡在
    一个完整的 TIME_WAIT 周期（Linux 写死 60s）里，而且看不出为什么。
    """
    port = _free_port()
    state = tmp_path / "state"
    state.mkdir()

    first = _run_launcher(tmp_path / "git", f"http://127.0.0.1:{port}", state)
    assert first.returncode == 0, first.stderr
    _terminate(int((state / "git-http.pid").read_text(encoding="utf-8").strip()))
    (state / "git-http.pid").unlink()

    # 先证对象：端口上确实还留着 TIME_WAIT（独占绑定绑不上），同时已经没有人在应答。
    assert _exclusive_bind_fails(port)
    assert start_e2e_git_source._port_has_listener("127.0.0.1", port) is False

    second = _run_launcher(tmp_path / "git", f"http://127.0.0.1:{port}", state)

    assert second.returncode == 0, second.stderr
    _terminate(int((state / "git-http.pid").read_text(encoding="utf-8").strip()))


def test_launcher_refuses_a_rival_git_source_and_prints_its_root_and_pid(tmp_path: Path) -> None:
    """必须失败臂：端口上是上一轮遗留的 Git 源孤儿时，必须拒绝并指认它是谁。

    ANTCODE_GATEWAY_E2E_KEEP=1 让孤儿成为常态，这就是 875e6dd 要挡的那个洞本身。
    与上面那条空 socket 占用不同，这里要求报出的不只是"被占了"，还得是运维照着就能
    动手的东西：孤儿在服务哪棵仓库树、PID 多少。
    """
    port = _free_port()
    rival_state = tmp_path / "rival"
    rival_state.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    rival = _run_launcher(tmp_path / "rival-git", f"http://127.0.0.1:{port}", rival_state)
    assert rival.returncode == 0, rival.stderr
    rival_pid = int((rival_state / "git-http.pid").read_text(encoding="utf-8").strip())

    try:
        result = _run_launcher(tmp_path / "git", f"http://127.0.0.1:{port}", state)
    finally:
        _terminate(rival_pid)

    assert result.returncode != 0
    assert f"端口 {port} 已被占用" in result.stderr
    # 报出来的必须是能直接照着办的东西：占的是哪棵仓库树、哪个 PID。
    assert f'"pid": {rival_pid}' in result.stderr
    assert str((tmp_path / "rival-git").resolve()) in result.stderr
    assert not (state / "git-http.pid").exists()


def test_unidentified_occupant_message_names_the_command_to_run() -> None:
    """认不出占用者时也不许回一句"无法识别"——运维看完要知道下一步敲什么。"""
    port = _free_port()

    message = start_e2e_git_source.describe_port_occupant(port)

    assert f"lsof -nP -iTCP:{port}" in message


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
