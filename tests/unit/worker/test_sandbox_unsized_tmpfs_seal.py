"""沙箱里另外两个 tmpfs——newroot 自身（``/``）与 ``--dev`` 建的 ``/dev``——的边界。

钉死的缺陷（真机复现，192.168.1.250 / antcode-worker，容器 mem_limit 4096MB、宿主
32GB、uid 1000 与生产任务同一个）：bwrap 的 ``--size`` 只认紧随其后的 ``--tmpfs``，这
两个挂载点都不是 ``--tmpfs`` 建的，于是内核按**宿主内存的一半**建盘——两处各报
16046MB，且 uid 1000 可写。``dd`` 往 ``/`` 写 500MB、往 ``/dev`` 写 300MB 全部成功，
容器 ``memory.current`` 从 112MB 涨到 973MB。这两条路径进程层三样都看不见：``write()``
的页不进 RSS、不计 RLIMIT_DATA，原本也不在内存盘采样范围里，连归因都没有。

与 ``test_sandbox_tmpfs_real_bwrap`` 的分工：那边的真机臂只写 ``/tmp``，测的是**已经
被 --size 修好的那个挂载点**，把结论推广到"这条路径"正是漏掉这两个的原因。本文件的
真机臂必须写 ``/`` 与 ``/dev``，并且每一条都带对照臂——剥掉 ``--remount-ro`` 后同样的
写入必须成功，否则证明不了"拦住的是本来会发生的事"。
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest
from antcode_worker.executor import resource_sampler
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_provider import BasicSandbox
from antcode_worker.executor.sandbox_scratch import (
    SANDBOX_TMPFS_MOUNTS,
    SEALED_TMPFS_MOUNTS,
    SIZED_TMPFS_MOUNTS,
)

_CAP_MB = 64
# 显著大于 _CAP_MB：万一这些字节其实落在 /tmp 上，ENOSPC 会立刻把用例判红，
# 而不是让"写成功了"与"写去了别处"长得一样。
_ROOT_WRITE_MB = 96
_ALIVE_SECONDS = 15
_POLL_SECONDS = 0.2
_SAMPLE_ROUNDS = 40
_RSS_CEILING_MB = 64
_RULE_CONNECTION_LIMIT = 8
_RULE_DURATION_LIMIT = 30
_SHORT_TEMP_ROOT = "/tmp"  # noqa: S108 — AF_UNIX 路径上限，见用到它的那条用例
_SEAL_TOKEN = "--remount-ro"
_REAL_BWRAP = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None,
    reason="需要 Linux bubblewrap",
)
# bwrap 里所有"会在 newroot 上建挂载点或改其属性"的参数。它们一旦排在 --remount-ro
# 之后，bwrap 要往只读的 newroot 上写，直接失败。
_MOUNT_TOKENS = frozenset(
    {
        "--bind",
        "--bind-try",
        "--ro-bind",
        "--ro-bind-try",
        "--dev-bind",
        "--dev-bind-try",
        "--bind-data",
        "--ro-bind-data",
        "--overlay",
        "--dir",
        "--tmpfs",
        "--dev",
        "--proc",
        "--mqueue",
        "--file",
        "--symlink",
        "--chmod",
        "--perms",
        "--size",
    }
)


def _sandbox(tmp_path) -> BasicSandbox:
    (tmp_path / "runtimes").mkdir(exist_ok=True)
    return BasicSandbox(
        SandboxConfig(
            sandbox_command=[shutil.which("bwrap") or "/usr/bin/bwrap"],
            network_isolated=True,
            data_dir=str(tmp_path),
            runtimes_dir=str(tmp_path / "runtimes"),
        )
    )


def _work_dir(tmp_path):
    work_dir = tmp_path / "runs" / "sources" / "run-1" / "project"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _wrap(tmp_path, script: str, *, extra_context: dict | None = None) -> list[str]:
    work_dir = _work_dir(tmp_path)
    context = {
        "work_dir": str(work_dir),
        "plugin_name": "code",
        "run_id": "run-1",
        "tmpfs_size_mb": _CAP_MB,
        **(extra_context or {}),
    }
    return _sandbox(tmp_path).wrap_command(["/bin/sh", "-c", script], context)


def _unsealed(command: list[str]) -> list[str]:
    """对照臂：只把 ``--remount-ro DEST`` 摘掉，其余 argv 一字不改。

    这是"改前"的精确复现——两臂唯一的差别就是本次改动加的那两对参数，任何一臂的结论
    都不能靠"另一次运行大概是这样"来推断。
    """
    stripped: list[str] = []
    index = 0
    while index < len(command):
        if command[index] == _SEAL_TOKEN:
            index += 2
            continue
        stripped.append(command[index])
        index += 1
    return stripped


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp/antcode-home"},
        capture_output=True,
        text=True,
        timeout=_ALIVE_SECONDS,
        check=False,
    )


def _sized_mounts(args: list[str]) -> set[str]:
    """把 ``--size N --tmpfs DEST`` 收成挂载点集合；只认紧挨着的三元组。"""
    return {args[i + 3] for i, token in enumerate(args) if token == "--size" and args[i + 2 : i + 3] == ["--tmpfs"]}


def _dd_script(target: str, size_mb: int) -> str:
    return f"dd if=/dev/zero of={target} bs=1M count={size_mb} 2>&1 | tail -1"


def test_the_sealed_list_is_exactly_the_tmpfs_that_no_size_can_reach(tmp_path) -> None:
    """能定尺寸的 / 只能封读写的，两份清单加起来必须是沙箱建的全部 tmpfs。

    少一个挂载点就是宿主内存一半那么大的可写盘，而且它既不在拦截清单里也不在归因清单
    里——``/`` 与 ``/dev`` 之前正是这样漏掉的。归因侧直接复用同一份对象，不是抄一份：
    两边分叉的表现是"限住了却看不见"。
    """
    args = _wrap(tmp_path, "true")

    assert _sized_mounts(args) == set(SIZED_TMPFS_MOUNTS)
    assert set(SIZED_TMPFS_MOUNTS).isdisjoint(SEALED_TMPFS_MOUNTS)
    assert SANDBOX_TMPFS_MOUNTS == (*SIZED_TMPFS_MOUNTS, *SEALED_TMPFS_MOUNTS)
    assert resource_sampler._SCRATCH_MOUNTS is SANDBOX_TMPFS_MOUNTS


def test_the_seal_comes_after_every_mount_argument(tmp_path) -> None:
    """封读写必须是最后一批文件系统参数，否则 bwrap 起不来。

    ``--remount-ro`` 之后再出现挂载参数，bwrap 要往只读的 newroot 上建挂载点并直接
    失败。这条断言盯的是"以后有人在末尾追加挂载"——那是本仓最常见的加法。
    """
    args = _wrap(tmp_path, "true")
    first_seal = args.index(_SEAL_TOKEN)
    payload_start = args.index("--")

    assert first_seal < payload_start, "封读写必须在 payload 分隔符之前"
    assert _MOUNT_TOKENS.isdisjoint(args[first_seal:payload_start])
    for mount in SEALED_TMPFS_MOUNTS:
        assert args[first_seal:payload_start].count(mount) == 1


def test_the_rule_egress_bridge_mount_still_lands_before_the_seal(monkeypatch, tmp_path) -> None:
    """Rule 的 egress bridge 挂载是在 filesystem 参数**之后**追加的，最容易越过封读写。

    它是本仓现存唯一一处"挂载参数不由 sandbox_filesystem_args 产出"的路径；封读写如果
    只跟在 filesystem 参数后面，这条 --ro-bind 就会落到只读 newroot 上。

    ``prlimit`` 是 util-linux 的命令，开发机（macOS）上没有；本用例只看 argv 顺序，
    与它无关，所以只把这一次查找定住（``shutil`` 是同一个模块对象，原函数必须先取出来
    再打补丁，否则 lambda 会递归调用自己）。
    """
    original_which = shutil.which
    monkeypatch.setattr(
        "antcode_worker.executor.sandbox_provider.shutil.which",
        lambda name: "/usr/bin/prlimit" if name == "prlimit" else original_which(name),
    )
    # AF_UNIX 路径有 ~104 字节硬上限，pytest 的 tmp_path 在 macOS 上就已经超了，
    # 所以 socket 单独放在短路径下，工作区仍用 tmp_path。
    with tempfile.TemporaryDirectory(dir=_SHORT_TEMP_ROOT) as short_root:
        socket_dir = Path(short_root) / "egress"
        socket_dir.mkdir()
        socket_dir.chmod(0o700)
        socket_path = socket_dir / "bridge.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(socket_path))
            socket_path.chmod(0o600)
            args = _wrap(
                tmp_path,
                "true",
                extra_context={
                    "plugin_name": "rule",
                    "rule_egress_socket": str(socket_path),
                    "payload_max_processes": _RULE_CONNECTION_LIMIT,
                    "rule_egress_max_duration_seconds": _RULE_DURATION_LIMIT,
                },
            )

    assert str(socket_dir) in args, "bridge 目录必须被挂载"
    assert args.index(str(socket_dir)) < args.index(_SEAL_TOKEN)


@_REAL_BWRAP
@pytest.mark.parametrize(("mount", "target"), [("/", "/big_root"), ("/dev", "/dev/big_dev")])
def test_real_sandbox_refuses_the_write_that_the_unsealed_profile_accepts(mount: str, target: str, tmp_path) -> None:
    """必须失败臂 + 改前对照：同一条 dd，封了写不进去，不封写得进去。

    只断言"写不进去"证明不了任何事——不带 ``--size`` 的 tmpfs 也可能因为别的原因失败。
    对照臂跑的是剥掉两对 ``--remount-ro`` 之后的同一份 argv，它成功才说明拦住的确实是
    本来会发生的那件事。归因不需要翻译：EROFS 自己点名了挂载点。
    """
    command = _wrap(tmp_path, _dd_script(target, _ROOT_WRITE_MB))

    sealed = _run(command)
    control = _run(_unsealed(command))

    assert "Read-only file system" in sealed.stdout, f"{mount} 必须拒绝写入: {sealed.stdout}"
    assert target in sealed.stdout, "报错必须点名被拒的路径，否则归因要靠猜"
    assert "copied" in control.stdout, f"改前同样条件必须能写进 {mount}: {control.stdout}"
    assert str(_ROOT_WRITE_MB * 1024 * 1024) in control.stdout, "对照臂必须真的把字节写下去"


@_REAL_BWRAP
def test_real_sandbox_keeps_every_path_a_normal_task_writes(tmp_path) -> None:
    """必须成功臂：封读写不能动任务真正要写的地方。

    收太紧比不收更糟。工作目录是 bind、``/tmp`` 与 ``/dev/shm`` 是定了尺寸的 tmpfs、
    HOME 在 ``/tmp`` 里、``/dev`` 下唯一该写的 ``/dev/shm`` 是独立挂载——``--remount-ro``
    不递归，这些一个都不该受影响；``/dev`` 的设备节点也必须照常可读可写。
    """
    checks = (
        "touch ./artifact && echo WORKDIR",
        "touch /tmp/x && echo TMP",
        "touch /dev/shm/x && echo SHM",
        "mkdir -p $HOME/.cache && echo HOME",
        "head -c 8 /dev/urandom > /tmp/r && echo URANDOM",
        "echo hi > /dev/null && echo DEVNULL",
    )
    expected = ["WORKDIR", "TMP", "SHM", "HOME", "URANDOM", "DEVNULL"]

    result = _run(_wrap(tmp_path, "; ".join(checks)))

    assert result.stdout.split() == expected, f"正常任务写入被打断: {result.stdout} {result.stderr}"
    assert result.returncode == 0


def _peak_scratch(command: list[str]) -> tuple[float, float]:
    """跑起来后轮询采样，返回（内存盘用量峰值 MB, 同一次采样的进程树 RSS MB）。"""
    process = subprocess.Popen(command, env={"PATH": "/usr/bin:/bin"})  # noqa: S603
    try:
        best = None
        for _ in range(_SAMPLE_ROUNDS):
            usage = resource_sampler.sample_process_tree(psutil.Process(process.pid))
            if usage is not None and (best is None or usage.scratch_used_bytes > best.scratch_used_bytes):
                best = usage
            if best is not None and best.scratch_used_mb >= _ROOT_WRITE_MB:
                break
            time.sleep(_POLL_SECONDS)
        assert best is not None, "采样器必须至少采到一次"
        return best.scratch_used_mb, best.memory_rss_mb
    finally:
        process.kill()
        process.wait(timeout=_ALIVE_SECONDS)


@_REAL_BWRAP
def test_the_sampler_attributes_bytes_written_to_the_root_tmpfs(tmp_path) -> None:
    """归因臂 + 对照：把 ``/`` 加进采样清单必须真的采得到，不能是一行不生效的常量。

    对照臂（剥掉封读写）往 ``/`` 写 96MB：采样器要看得见这些字节，而进程树 RSS 看不见
    ——后者正是三层防护同时失明的那一层。封读写之后同一份 payload 写不进去，采样值自然
    回落，两个数一起才说明"限住了"与"看得见"是同一件事的两面。
    """
    script = f"{_dd_script('/big_root', _ROOT_WRITE_MB)}; sleep {_ALIVE_SECONDS}"
    command = _wrap(tmp_path, script)

    control_scratch, control_rss = _peak_scratch(_unsealed(command))
    sealed_scratch, _sealed_rss = _peak_scratch(command)

    assert control_scratch >= _ROOT_WRITE_MB, "改前写进 / 的字节必须被内存盘采样看见"
    assert control_rss < _RSS_CEILING_MB, "这些字节绝不该出现在进程树 RSS 里"
    assert sealed_scratch < _ROOT_WRITE_MB, "封读写之后 / 上不该再有可采到的用量"
