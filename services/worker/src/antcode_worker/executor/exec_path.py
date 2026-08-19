"""子进程 PATH 的唯一构造实现。

历史缺陷与 PYTHONPATH 同源（见 ``python_path``）：``CodePlugin._build_env`` 给 Node
项目拼的 ``node_modules/.bin`` 先被 ``sandbox_plan`` 覆盖一次、再被
``ProcessExecutor._build_env`` 覆盖一次，Node 项目永远用不上本地 CLI。归属裁定：
**进程层是最终权威**——``ExecPlan.env`` 是扁平 dict，Worker 写的 PATH 与任务注入的
PATH 不可区分，信任上游等于重开 P0-01 的 PATH 注入；沙箱层沿用同一函数，保证它
交给下游的计划不是一份会被悄悄改写的半真值。

P0-01 边界未被放宽：本函数**只推导、不接受**。``node_modules/.bin`` 由 Worker 从
``exec_plan.cwd`` 拼出（解包根下经校验的项目目录），``exec_plan.env["PATH"]`` 依旧
整条丢弃，任务无法把任意目录送进 PATH。
"""

from __future__ import annotations

import os

_NODE_LOCAL_BIN_PARTS = ("node_modules", ".bin")


def authoritative_path(work_dir: str | None, runtime_path: str) -> str:
    """按固定优先级拼出 Worker 权威 PATH。

    ``work_dir`` 取子进程真正的工作目录，两层必须传同一个值（沙箱层传它写进
    沙箱化计划的那个 cwd，进程层传计划里的 cwd），否则又会退回"两层各算一份"。

    顺序即信任级别，不可调换：

    1. runtime 的 ``bin``：首项必须是 Worker 值（P0-01 不变量）。
    2. 项目本地 ``node_modules/.bin``：排在 runtime 之后，遮蔽不了 Worker 运行时；
       排在宿主 PATH 之前，才符合 npm "本地依赖优先于全局" 的既定语义。它能遮蔽的
       只有系统二进制，而那只影响任务自己派生的子进程——同一沙箱、同一 uid、同一份
       任务自有的 ``node_modules``，不跨任何权限边界。
    3. 宿主 PATH。
    """
    entries = [runtime_bin(runtime_path)]
    local_bin = _node_local_bin(work_dir)
    if local_bin:
        entries.append(local_bin)
    host_path = os.environ.get("PATH", "")
    if host_path:
        entries.append(host_path)
    return os.pathsep.join(entries)


def runtime_bin(runtime_path: str) -> str:
    """runtime 目录下的可执行文件目录（Windows 是 ``Scripts``）。"""
    return os.path.join(runtime_path, "Scripts" if os.name == "nt" else "bin")


def _node_local_bin(work_dir: str | None) -> str | None:
    """任务工作目录里的 Node 本地 CLI 目录；不存在则返回 None。

    存在性检查在宿主上做，而沙箱对工作目录用的是同路径 bind（``--bind p p``），
    因此宿主可见即沙箱内可见。依赖装配 (`_prepare_deps`) 在计划构建期就已完成，
    到这里 ``node_modules`` 必然已经落盘——插件层那次检查则恒早于装配，必然落空。
    """
    if not work_dir:
        return None
    local_bin = os.path.join(work_dir, *_NODE_LOCAL_BIN_PARTS)
    return local_bin if os.path.isdir(local_bin) else None


__all__ = ["authoritative_path", "runtime_bin"]
