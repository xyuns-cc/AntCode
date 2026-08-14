"""
子进程组信号

按进程组向任务子进程发信号，连带覆盖它 fork 出来的孙进程。
从 ``process.py`` 拆出，与 ``process_limits`` 的进程组隔离逻辑配套。
"""

import asyncio
import contextlib
import os

from loguru import logger


def signal_process_group(process: asyncio.subprocess.Process, sig: int) -> None:
    """优先按进程组发送信号（连带杀掉子进程 fork 出来的孙进程）。

    拿不到进程组时退回到只给主进程发信号。
    """
    if process.returncode is not None:
        return
    if _kill_process_group(process.pid, sig):
        return
    with contextlib.suppress(ProcessLookupError):
        process.send_signal(sig)


def _kill_process_group(pid: int, sig: int) -> bool:
    """向 ``pid`` 所属进程组发送信号，成功返回 True。

    B7: ``build_preexec_fn`` 已保证 setsid 失败的子进程根本不会启动；这里再校验
    一次进程组不等于 Worker 自身——一旦相等就绝不能 killpg，否则会把 Worker 主进程
    连同所有兄弟任务一起杀掉。
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    if pgid == os.getpgrp():
        logger.error(f"子进程 pgid={pgid} 与 Worker 自身相同，拒绝 killpg（会自杀），改为只向 pid={pid} 发信号")
        return False
    try:
        os.killpg(pgid, sig)
    except OSError as exc:
        logger.warning(f"killpg 失败，退回单进程信号: pgid={pgid} sig={sig} err={exc}")
        return False
    return True
