"""未绑定项目运行时的任务如何拿到解释器。

真机事故：engine 对"既无 ``labels.runtime_env_name`` 也无 ``runtime_spec``"的任务
去建项目级 venv，必然失败在 "runtime python_spec.path 或 python_spec.version
不能为空"，rule 任务 100% exit 1。

而 rule 是 Worker 内建能力：插件恒用 ``sys.executable``
（``plugins/rule/plugin.py::_get_python_executable``，其文档明确说明用用户 venv 会
立刻 ``ModuleNotFoundError``），项目侧也按设计不绑定运行时
（``ProjectRuleCreateRequest.runtime_scope`` 描述："规则项目不绑定 Worker 运行时"）。

这不是放宽校验：code / render 插件对缺失 ``runtime_spec.python_path`` 各自有显式
raise，拿不到用户 venv 依旧 fail-closed。
"""

from __future__ import annotations

import sys
from typing import Any

WORKER_PYTHON_RUNTIME_HASH = "worker-python"


def worker_python_runtime_handle() -> Any:
    """交出 Worker 自己的解释器作为运行时句柄。"""
    from antcode_worker.domain.models import RuntimeHandle

    # path 是运行时根目录，沙箱要 ro-bind 它（空串会被 sandbox 判为非法）。
    # Worker 自身解释器的根就是 sys.prefix（容器内即 /app/.venv），
    # rule 依赖的 Scrapy / scrapy-playwright 都装在那里。
    return RuntimeHandle(
        path=sys.prefix,
        runtime_hash=WORKER_PYTHON_RUNTIME_HASH,
        python_executable=sys.executable,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )


__all__ = ["WORKER_PYTHON_RUNTIME_HASH", "worker_python_runtime_handle"]
