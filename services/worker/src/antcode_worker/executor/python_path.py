"""子进程 PYTHONPATH 的唯一构造实现。

历史缺陷：沙箱层与进程层各算一份 PYTHONPATH，而 ``ProcessExecutor._build_env``
是子进程启动前的最后一个写者，它算出的短值把沙箱层的长值整个盖掉——沙箱层
补的 bundle 根与受信内建包根从未到达子进程。归属裁定：**进程层是最终权威**
（它必须重算，因为 ``ExecPlan.env`` 里 Worker 写的值和任务注入的值不可区分，
信任上游等于放开 P0-01 的 PYTHONPATH 注入）；沙箱层沿用同一函数，保证它交给
下游的计划不是一份会被悄悄改写的半真值。
"""

from __future__ import annotations

import os
from pathlib import Path

from antcode_worker.domain.models import ExecPlan

# 这些插件的载荷是 Worker 自带的 Python 入口（rule/spider 的 antcode_scrapy、
# render 的 _runner），源码布局部署时它们不在任务 runtime 的 site-packages 里。
_INTERNAL_PYTHON_PLUGINS = frozenset({"render", "rule", "spider"})
_TRUSTED_PACKAGES = ("antcode_worker", "antcode_scrapy", "antcode_core", "antcode_contracts")


def authoritative_python_path(exec_plan: ExecPlan, runtime_path: str) -> str:
    """按固定优先级拼出 Worker 权威 PYTHONPATH。

    顺序即信任级别，不可调换：

    1. ``runtime_path``：任务 runtime，首项必须是 Worker 值（P0-01 不变量）。
    2. 受信内建包源码根：排在 bundle 之前，任务代码无法遮蔽 ``antcode_*``。
    3. ``workspace_root``（source bundle 解包根）：``include_paths`` 共享目录是
       cwd 的兄弟节点，不在这里 ``import libs.common.x`` 就找不到。放在最后，
       它只能遮蔽 site-packages——而 cwd 早已在 ``sys.path[0]`` 具备同等能力。
    """
    roots = [runtime_path]
    if exec_plan.plugin_name in _INTERNAL_PYTHON_PLUGINS:
        roots.extend(_trusted_python_source_roots())
    if exec_plan.workspace_root:
        roots.append(exec_plan.workspace_root)
    return os.pathsep.join(dict.fromkeys(roots))


def _trusted_python_source_roots() -> tuple[str, ...]:
    roots: list[str] = []
    for package_name in _TRUSTED_PACKAGES:
        package = __import__(package_name)
        package_file = package.__file__
        if not package_file:
            raise RuntimeError(f"沙箱内建包缺少文件路径: {package_name}")
        package_dir = Path(package_file).resolve(strict=True).parent
        roots.append(str(package_dir.parent))
    return tuple(dict.fromkeys(roots))


__all__ = ["authoritative_python_path"]
