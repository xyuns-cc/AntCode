"""`infra/docker/healthcheck.sh` 的真实执行测试。

这里不 mock 任何东西，直接用 /bin/sh 跑真脚本、读真状态文件。唯一被拦截的是
`kill` —— 它是普通内建命令，POSIX 规定同名函数优先于普通内建，所以测试把脚本
`.` 进一个定义了 `kill` 函数的 shell 里，就能在**不真的给 PID 1 发信号**的前提下
断言"重启决策是否发生"。拦的是不可逆副作用，不是被测逻辑。

判据来自缺陷本身：`retries` 与 `start_period` 必须真的生效——
第一次失败不能重启，宽限期内不能重启，探针卡死也必须能攒够次数。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path("infra/docker/healthcheck.sh").resolve()
KILL_MARKER = "RESTART-REQUESTED"
EXIT_USAGE = 2
MAX_FAILURES = 3
NO_GRACE = 0
LONG_GRACE = 3600
HANG_TIMEOUT_SECONDS = 0.4


def _run(state_dir: Path, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """把脚本 source 进一个 stub 了 `kill` 的 sh，返回真实执行结果。"""
    quoted = " ".join(f"'{arg}'" for arg in args)
    program = f"kill() {{ echo '{KILL_MARKER}' \"$@\"; }}\nset -- {quoted}\n. '{SCRIPT}'\n"
    return subprocess.run(
        ["/bin/sh", "-c", program],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "ANTCODE_HEALTHCHECK_STATE_DIR": str(state_dir)},
        check=False,
    )


def _failures(state_dir: Path) -> int:
    return int((state_dir / "consecutive-failures").read_text().strip())


def _restart_requested(result: subprocess.CompletedProcess[str]) -> bool:
    return KILL_MARKER in result.stdout


def test_successful_probe_exits_zero_and_never_restarts(tmp_path: Path) -> None:
    result = _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "true")

    assert result.returncode == 0, result.stderr
    assert not _restart_requested(result)
    assert _failures(tmp_path) == 0


def test_first_failure_does_not_restart(tmp_path: Path) -> None:
    """核心判据：kill 内联在 test 里时，这一次就已经把 PID 1 杀了。"""
    result = _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "false")

    assert result.returncode == 1
    assert not _restart_requested(result)
    assert _failures(tmp_path) == 1


def test_restart_only_after_retries_consecutive_failures(tmp_path: Path) -> None:
    """连续失败次数必须真的攒到 retries 才重启，一次不多一次不少。"""
    before_threshold = [_run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "false") for _ in range(MAX_FAILURES - 1)]
    at_threshold = _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "false")

    assert not any(_restart_requested(result) for result in before_threshold)
    assert _restart_requested(at_threshold)
    assert at_threshold.returncode == 1
    assert f"阈值 {MAX_FAILURES}" in at_threshold.stderr


def test_success_resets_the_failure_streak(tmp_path: Path) -> None:
    """ "连续"是字面意思：中间成功一次，之前的失败不再算数。"""
    for _ in range(MAX_FAILURES - 1):
        _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "false")
    _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "true")
    after_reset = [_run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "false") for _ in range(MAX_FAILURES - 1)]

    assert not any(_restart_requested(result) for result in after_reset)
    assert _failures(tmp_path) == MAX_FAILURES - 1


def test_start_period_suppresses_restart_while_grace_has_not_elapsed(tmp_path: Path) -> None:
    """start_period 也必须真的生效：启动慢的容器不能在宽限期内被打死。"""
    results = [_run(tmp_path, str(MAX_FAILURES), str(LONG_GRACE), "false") for _ in range(MAX_FAILURES + 2)]

    assert not any(_restart_requested(result) for result in results)
    assert all(result.returncode == 1 for result in results)
    assert _failures(tmp_path) > MAX_FAILURES


def test_hung_probe_still_accumulates_and_eventually_restarts(tmp_path: Path) -> None:
    """探针卡死是最该重启的场景，也是计数最容易丢的场景。

    Docker 会在 `timeout` 到点时杀掉整个 test 命令；如果计数写在探测之后，
    卡死的容器永远攒不到 retries，自愈彻底失效。这里用真实超时杀进程复现。
    """
    for _ in range(MAX_FAILURES):
        try:
            _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "sleep 30", timeout=HANG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    assert _failures(tmp_path) == MAX_FAILURES
    next_probe = _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), "sleep 30", timeout=HANG_TIMEOUT_SECONDS)
    assert _restart_requested(next_probe)


def test_wrong_argument_count_fails_loudly(tmp_path: Path) -> None:
    """参数配错必须立刻报错，不能退化成"探针恒通过"。"""
    result = _run(tmp_path, str(MAX_FAILURES), "false")

    assert result.returncode == EXIT_USAGE
    assert "用法" in result.stderr


def test_probe_runs_through_a_shell_so_pipelines_and_quotes_survive(tmp_path: Path) -> None:
    """compose 里的 redis / backup 探针带管道与变量展开，必须原样可用。"""
    probe = 'printf PONG | grep -q PONG && test "$((1 + 1))" -eq 2'

    assert _run(tmp_path, str(MAX_FAILURES), str(NO_GRACE), probe).returncode == 0


def test_script_is_executable_so_the_bind_mount_can_be_invoked_directly() -> None:
    """compose 以 `/usr/local/bin/antcode-healthcheck` 直接调用它，bind mount 会
    原样带上宿主的权限位；仓库里丢了 +x，所有 healthcheck 会变成 126 恒失败。"""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "infra/docker/healthcheck.sh"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert listing.stdout.split()[0] == "100755", listing.stdout
