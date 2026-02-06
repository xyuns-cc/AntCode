"""
代码执行插件

Requirements: 8.3
"""

import os

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase


class CodePlugin(PluginBase):
    """
    代码执行插件

    生成 Python 代码执行的 ExecPlan。

    Requirements: 8.3
    """

    @property
    def name(self) -> str:
        return "code"

    @property
    def priority(self) -> int:
        return 10

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.CODE

    def validate(self, payload: TaskPayload) -> list[str]:
        errors = []
        if not payload.entry_point:
            errors.append("entry_point 不能为空")
        return errors

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        # 确定 Python 可执行文件
        python_exe = self._get_python_executable(context)

        # 构建命令参数
        args = [payload.entry_point]
        args.extend(payload.args)

        # 构建环境变量
        env = dict(payload.env_vars)
        if payload.project_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.project_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.project_path

        # 工作目录
        cwd = payload.project_path or os.getcwd()

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=payload.artifact_patterns,
        )

    def _get_python_executable(self, context: RunContext) -> str:
        """获取 Python 可执行文件路径"""
        # 如果有运行时规格，使用指定的 Python
        if context.runtime_spec and context.runtime_spec.python_path:
            return context.runtime_spec.python_path

        # 默认使用系统 Python
        import sys
        return sys.executable
