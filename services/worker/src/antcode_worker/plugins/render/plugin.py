"""渲染任务插件。"""

import json
import os
from typing import Any

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase

# 独立 runner 脚本的绝对路径。走 argv/env 传参，杜绝 `python -c` 拼源码注入。
_RUNNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_runner.py")

# 用户可控上下文数据通过该 env 变量传给 runner（JSON 字符串）。
_CONTEXT_ENV_VAR = "ANTCODE_RENDER_CONTEXT_JSON"


class RenderPlugin(PluginBase):
    """
    渲染任务插件

    生成渲染任务执行的 ExecPlan。
    支持多种非浏览器渲染引擎：Jinja2、Mako、脚本。

    Requirements: 8.5
    """

    @property
    def name(self) -> str:
        return "render"

    @property
    def priority(self) -> int:
        return 15  # 介于 spider 和 code 之间

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.RENDER

    def validate(self, payload: TaskPayload) -> list[str]:
        errors: list[str] = []
        render_config = self._extract_render_config(payload)
        engine = render_config.get("engine", "jinja2")

        if engine in ("jinja2", "mako"):
            if not payload.entry_point:
                errors.append("entry_point 不能为空（模板文件路径）")
                return errors
            # T6：template_path / output_file / template_dir 都是用户可控字符串，
            # 最终会以命令行参数进入 subprocess 并被 jinja2/mako 打开。必须校验：
            #   1. 相对路径（禁绝对路径、Windows 盘符）
            #   2. 不含 ".." 段
            #   3. 相对 workspace/project_cwd 解析后仍在 workspace 内
            base = payload.project_cwd or payload.workspace_path
            for field, value in (
                ("entry_point", payload.entry_point),
                ("output_file", render_config.get("output_file") or "output.html"),
                ("template_dir", render_config.get("template_dir")),
            ):
                if value is None or value == "":
                    continue
                err = self._validate_relative_path(field, value, base)
                if err:
                    errors.append(err)
            return errors
        if engine == "script":
            if not payload.entry_point:
                errors.append("script 模式需要 entry_point")
            return errors

        return [f"不支持的 render engine: {engine}"]

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """
        构建渲染执行计划

        支持的渲染引擎：
        1. jinja2: Jinja2 模板渲染
        2. mako: Mako 模板渲染
        3. script: 自定义渲染脚本
        """
        python_exe = self._get_python_executable(context)
        render_config = self._extract_render_config(payload)
        engine = render_config.get("engine", "jinja2")

        if engine in ("jinja2", "mako"):
            return self._build_template_plan(python_exe, context, payload, render_config)
        if engine == "script":
            return self._build_script_plan(python_exe, context, payload, render_config)
        raise ValueError(f"不支持的 render engine: {engine}")

    def _extract_render_config(self, payload: TaskPayload) -> dict[str, Any]:
        """从 payload 提取渲染配置"""
        config = {
            "engine": payload.kwargs.get("engine", "jinja2"),
            "output_file": payload.kwargs.get("output_file", "output.html"),
            "output_format": payload.kwargs.get("output_format", "html"),
            "template_dir": payload.kwargs.get("template_dir"),
            "context_data": payload.kwargs.get("context_data", {}),
        }
        return config

    def _build_template_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建模板渲染执行计划

        改用独立 runner 脚本 + argv 传参，避免 `python -c` 拼字符串导致的模板参数
        注入 RCE（T6：击穿 render-only worker 能力边界）。所有用户可控数据（路径、
        context_data）都走 argv 或 env，不再进入 Python 源码。
        """
        engine = config.get("engine", "jinja2")
        template_path = payload.entry_point
        output_file = config.get("output_file") or "output.html"
        template_dir = config.get("template_dir") or "."
        context_data = config.get("context_data", {})

        # 二次防御：即便 validate() 被绕过，构建时再校验一次路径，防御性拒绝越界值。
        base = payload.project_cwd or payload.workspace_path
        for field, value in (
            ("entry_point", template_path),
            ("output_file", output_file),
            ("template_dir", template_dir),
        ):
            err = self._validate_relative_path(field, value, base)
            if err:
                raise ValueError(err)

        args = [
            _RUNNER_PATH,
            "--engine",
            engine,
            "--template-path",
            template_path,
            "--output-file",
            output_file,
            "--template-dir",
            template_dir,
        ]

        # 环境变量
        env = dict(payload.env_vars)
        if payload.workspace_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.workspace_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.workspace_path

        # context_data 走 env，保留 repr 语义（json.dumps 已足够安全，env 值不进
        # subprocess argv 也不进 shell）。
        env[_CONTEXT_ENV_VAR] = json.dumps(context_data)

        cwd = self._get_project_cwd(payload)

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        artifact_patterns.append(output_file)

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=artifact_patterns,
        )

    def _build_script_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建脚本模式执行计划"""
        args = [payload.entry_point]
        args.extend(payload.args)

        # 环境变量
        env = dict(payload.env_vars)
        if payload.workspace_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.workspace_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.workspace_path

        # 渲染特定环境变量
        if config.get("output_file"):
            env["RENDER_OUTPUT_FILE"] = config["output_file"]
        if config.get("output_format"):
            env["RENDER_OUTPUT_FORMAT"] = config["output_format"]

        cwd = self._get_project_cwd(payload)

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        if config.get("output_file"):
            artifact_patterns.append(config["output_file"])

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=artifact_patterns,
        )

    # ---------- 路径校验 ----------

    @staticmethod
    def _validate_relative_path(
        field_name: str,
        value: str,
        base: str | None,
    ) -> str | None:
        """校验用户传入路径相对且落在 workspace 内。

        与 ``CodePlugin._validate_entry_point`` 对齐（services/worker/src/antcode_worker/
        plugins/code/plugin.py:74），render 只允许修改 plugin.py + 新建 _runner.py，
        因此这里就地实现一份，未抽到公共模块。

        返回错误信息或 None（校验通过）。
        """
        if not isinstance(value, str):
            return f"{field_name} 不合法：期望字符串，得到 {type(value).__name__}"
        raw = value
        entry = value.strip().replace("\\", "/")
        if not entry:
            return f"{field_name} 不能为空"
        # 拒绝绝对路径（POSIX 前导 '/'、Windows 盘符 'C:' 等）
        if entry.startswith("/") or (len(entry) >= 2 and entry[1] == ":"):
            return f"{field_name} 不合法：不允许绝对路径 ({raw})"
        # 拒绝路径穿越
        if ".." in entry.split("/"):
            return f"{field_name} 不合法：不允许包含 '..' ({raw})"
        # commonpath 兜底：解析后必须仍在 base 内
        if base:
            try:
                base_abs = os.path.realpath(base)
                target = os.path.realpath(os.path.join(base_abs, entry))
                if os.path.commonpath([base_abs, target]) != base_abs:
                    return f"{field_name} 越界（不在项目工作目录内）: {raw}"
            except (ValueError, OSError) as exc:
                return f"{field_name} 校验失败: {exc}"
        return None

    def _get_python_executable(self, context: RunContext) -> str:
        """获取 Python 可执行文件路径"""
        if context.runtime_spec and context.runtime_spec.python_path:
            return context.runtime_spec.python_path
        raise RuntimeError("runtime_spec.python_path 不能为空")

    def _get_project_cwd(self, payload: TaskPayload) -> str:
        cwd = payload.project_cwd or payload.workspace_path
        if not cwd:
            raise RuntimeError("project_cwd 不能为空")
        return cwd
