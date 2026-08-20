"""D1/D2: 单任务内存上限限的是"可写数据段"而不是"虚拟地址空间"。

背景（真机复验 `c079620` 抓到的两个缺陷，同一个根因）：
``task_memory_limit_mb`` 曾被映射成 ``RLIMIT_AS``，而 ``RLIMIT_AS`` 限的是虚拟地址
空间。现代运行时会 PROT_NONE 预留远超实际用量的地址区间（预留不占物理页）：

- JVM 光 compressed class space 就保留 1GiB，31GB/8 核机器算出的自适应默认值
  2808MB 撑不住 → **Java 在默认配置下 100% 起不来 JVM**；
- tsx 的 V8 WASM 要 32~64GB 地址空间，而 ``task_memory_limit_mb`` 的 API 上限只有
  8192MB → **TypeScript 在任何可配置值下都跑不了**，抬上限无解。

``RLIMIT_DATA`` 自 Linux 4.7 起覆盖 brk 与私有可写映射，PROT_NONE 预留不计入，
因此收的是"真的会写下去的内存"。

**这些用例能证明什么、不能证明什么**（本仓刚出过一次假绿：某 TS 单测用 tmp_path
造的假 node，从不真跑 tsx，因此完全抓不到 WASM 地址空间问题）：

- ``test_memory_limit_*`` / ``test_dependency_*`` / ``test_sandbox_limits_describe_*``
  只证明**映射关系**——限额落到了 ``RLIMIT_DATA`` 这一项上、且不再落到 ``RLIMIT_AS``。
  它们不启动任何真实运行时，**不能**证明 JVM 或 tsx 真的跑得起来。
- ``test_reserved_address_space_*`` 是唯一一条真跑内核的用例：它在 fork 出来的子进程
  里真的 ``setrlimit`` 并真的 ``mmap`` 一段 PROT_NONE 预留，复现 JVM/V8 的分配形态，
  证明"同一个数值下 RLIMIT_AS 拒绝而 RLIMIT_DATA 放行"。仅 Linux 有此语义，
  macOS 上两项 rlimit 都拒绝下调，故 skip——**开发机门禁上这条是 skip 状态**。
- "Java/TypeScript 端到端真的能跑"只能由真机派发链路证明，单测一律证不了。
"""

from __future__ import annotations

import mmap
import os
import sys
from datetime import datetime
from typing import Any, cast
from unittest.mock import patch

import pytest
from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import ExecPlan
from antcode_worker.executor import process as process_mod
from antcode_worker.executor import process_limits
from antcode_worker.runtime import dependency_rlimits

_BYTES_PER_MIB = 1024 * 1024
_MEMORY_LIMIT_MB = 256
_CPU_SECONDS = 10
_MAX_OPEN_FILES = 64
_MAX_PROCESSES = 32
_FILE_SIZE_MB = 8

# 模拟 JVM/V8 的地址空间预留：远大于内存上限，但一个字节都不会写。
_RESERVED_ADDRESS_SPACE_BYTES = 8 * 1024 * _BYTES_PER_MIB

# fork 子进程的退出码，用于把内核的判定带回父进程
_EXIT_RESERVE_ALLOWED = 0
_EXIT_RESERVE_REFUSED = 3
_EXIT_SETRLIMIT_REFUSED = 4

# 内存超限时监控杀掉的是整棵进程树，子进程退出码恒为 -SIGKILL
_SIGKILL_EXIT_CODE = -9
_PEAK_OVER_LIMIT_MB = _MEMORY_LIMIT_MB + 1


def _limits() -> process_limits.SandboxLimits:
    return process_limits.SandboxLimits(
        enforce_rlimit=True,
        cpu_seconds=_CPU_SECONDS,
        memory_mb=_MEMORY_LIMIT_MB,
        max_open_files=_MAX_OPEN_FILES,
        max_processes=_MAX_PROCESSES,
        file_size_mb=_FILE_SIZE_MB,
    )


def _requested_names() -> dict[str, int]:
    return {item.limit_name: item.limit_value for item in _limits().requested_rlimits()}


def test_memory_limit_maps_to_data_segment_not_address_space() -> None:
    """任务子进程的内存上限必须落在 RLIMIT_DATA 上，且不得再落到 RLIMIT_AS。"""
    requested = _requested_names()

    assert requested["RLIMIT_DATA"] == _MEMORY_LIMIT_MB * _BYTES_PER_MIB
    assert "RLIMIT_AS" not in requested, "RLIMIT_AS 限虚拟地址空间，JVM/V8 的地址预留会被误判成内存占用"


def test_memory_limit_of_zero_requests_no_memory_rlimit() -> None:
    """0 表示不限制——不能退化成"限成 0 字节"把所有任务锁死。"""
    unlimited = process_limits.SandboxLimits(
        enforce_rlimit=True,
        cpu_seconds=_CPU_SECONDS,
        memory_mb=0,
        max_open_files=_MAX_OPEN_FILES,
        max_processes=_MAX_PROCESSES,
        file_size_mb=_FILE_SIZE_MB,
    )
    names = {item.limit_name for item in unlimited.requested_rlimits()}

    assert "RLIMIT_DATA" not in names
    assert "RLIMIT_AS" not in names


def test_sandbox_limits_describe_names_the_memory_rlimit() -> None:
    """沙箱起不来时的错误信息必须点名限的是哪一项，否则会把人引向错误的量级判断。"""
    described = _limits().describe()

    assert "RLIMIT_DATA" in described
    assert f"mem={_MEMORY_LIMIT_MB}MB" in described


def test_dependency_preexec_limits_data_segment_not_address_space() -> None:
    """装依赖跑的是 npm/node，同样不能按虚拟地址空间收费。

    真机实测：同一个 ``npm install --offline``，RLIMIT_AS 512MB（该路径的默认值）
    下 node 直接 "Fatal process out of memory" 核心转储。
    """
    import resource as posix_resource

    applied: dict[int, int] = {}

    def _record(kind: int, limits: tuple[int, int]) -> None:
        applied[kind] = limits[0]

    # 宿主 hard limit 报成无限，_set_limit 才会原样施加请求值而不是取 min 后截断。
    unlimited = (posix_resource.RLIM_INFINITY, posix_resource.RLIM_INFINITY)
    with (
        patch.object(dependency_rlimits.os, "setsid"),
        patch.object(dependency_rlimits.resource, "getrlimit", return_value=unlimited),
        patch.object(dependency_rlimits.resource, "setrlimit", side_effect=_record),
    ):
        dependency_rlimits.build_dependency_preexec(cpu_seconds=_CPU_SECONDS, memory_mb=_MEMORY_LIMIT_MB)()

    assert applied[posix_resource.RLIMIT_DATA] == _MEMORY_LIMIT_MB * _BYTES_PER_MIB
    assert posix_resource.RLIMIT_AS not in applied


def _reserve_address_space_in_child(limit_name: str) -> int:
    """在子进程里施加 rlimit 并预留一段 PROT_NONE 地址空间，返回退出码。"""
    import resource as posix_resource

    limit_bytes = _MEMORY_LIMIT_MB * _BYTES_PER_MIB
    pid = os.fork()
    if pid == 0:  # pragma: no cover - 子进程分支不参与父进程的覆盖率统计
        try:
            posix_resource.setrlimit(getattr(posix_resource, limit_name), (limit_bytes, limit_bytes))
        except (OSError, ValueError):
            os._exit(_EXIT_SETRLIMIT_REFUSED)
        try:
            mmap.mmap(
                -1,
                _RESERVED_ADDRESS_SPACE_BYTES,
                flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                prot=0,
            )
        except OSError:
            os._exit(_EXIT_RESERVE_REFUSED)
        os._exit(_EXIT_RESERVE_ALLOWED)
    _pid, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status), "子进程必须正常退出，否则判定不可信"
    return os.WEXITSTATUS(status)


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_DATA 只在 Linux 上覆盖私有可写映射")
def test_reserved_address_space_is_admitted_by_data_limit_and_refused_by_address_space_limit() -> None:
    """真跑内核：同一个数值下，地址预留在 RLIMIT_AS 被拒、在 RLIMIT_DATA 放行。

    这正是 JVM（compressed class space 保留 1GiB）与 tsx 的 V8 WASM（32~64GB）
    的分配形态。不写一个字节，所以 RLIMIT_DATA 不该计费。
    """
    assert _reserve_address_space_in_child("RLIMIT_DATA") == _EXIT_RESERVE_ALLOWED
    assert _reserve_address_space_in_child("RLIMIT_AS") == _EXIT_RESERVE_REFUSED


def test_memory_breach_is_reported_as_oom_even_when_killed_by_signal() -> None:
    """限额生效的证据不能被 SIGKILL 掩盖成一句笼统的"进程被终止"。

    RLIMIT_DATA 不覆盖 MAP_SHARED 与 tmpfs，那部分由进程树 RSS 监控主动杀进程组
    兑现——这条路径的退出码恒为 -SIGKILL。判定顺序错了，用户就看不出任务是撞了
    内存上限还是被别人杀了。
    """
    info = process_mod.ProcessInfo(
        process=cast(Any, None),
        run_id="run-oom",
        started_at=datetime.now(),
        exec_plan=ExecPlan(command="/usr/bin/true", memory_limit_mb=_MEMORY_LIMIT_MB),
    )
    info.memory_peak_mb = _PEAK_OVER_LIMIT_MB

    status, reason, message = process_mod.ProcessExecutor()._determine_result(_SIGKILL_EXIT_CODE, info)

    assert reason is ExitReason.OOM
    assert status is RunStatus.FAILED
    assert message == "内存超限"


def test_signal_kill_without_breach_still_reports_killed() -> None:
    """没超限就还是"被终止"——上面的顺序调整不能把所有 SIGKILL 都染成 OOM。"""
    info = process_mod.ProcessInfo(
        process=cast(Any, None),
        run_id="run-killed",
        started_at=datetime.now(),
        exec_plan=ExecPlan(command="/usr/bin/true", memory_limit_mb=_MEMORY_LIMIT_MB),
    )

    status, reason, _message = process_mod.ProcessExecutor()._determine_result(_SIGKILL_EXIT_CODE, info)

    assert reason is ExitReason.KILLED
    assert status is RunStatus.KILLED
