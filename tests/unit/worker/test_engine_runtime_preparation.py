"""未绑定运行时的项目（rule）必须能拿到 Worker 自己的解释器。

真机事故：engine._prepare_runtime 对"既无 labels.runtime_env_name 也无
runtime_spec"的任务去建项目级 venv，必然失败在
"runtime python_spec.path 或 python_spec.version 不能为空"，rule 任务 100% exit 1。
而 rule 是 Worker 内建能力，插件本就恒用 sys.executable
（plugins/rule/plugin.py::_get_python_executable），项目按设计不绑定运行时
（ProjectRuleCreateRequest.runtime_scope 描述："规则项目不绑定 Worker 运行时"）。
"""

import sys

from antcode_worker.engine.unbound_runtime import worker_python_runtime_handle


def test_unbound_project_falls_back_to_worker_interpreter():
    handle = worker_python_runtime_handle()

    assert handle.python_executable == sys.executable
    assert handle.runtime_hash == "worker-python"
    # 沙箱要 ro-bind 运行时根目录，空串会被判非法（真机报
    # "sandbox context.runtime_path 必须是非空绝对路径"）。
    assert handle.path == sys.prefix
    assert handle.path.startswith("/")
    assert handle.python_version == f"{sys.version_info.major}.{sys.version_info.minor}"
