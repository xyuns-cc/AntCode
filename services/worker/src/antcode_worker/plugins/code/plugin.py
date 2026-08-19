"""代码执行插件（多语言 + 依赖自动装配）。

按 entry_point 后缀自动选择语言运行时：
- .py / .pyw          → Python（走现有 uv/mise venv 的 python 可执行）
- .js / .mjs / .cjs   → Node.js（用系统/mise 的 node）
- .ts / .mts / .cts   → Node.js（要求 workspace 里 devDep 装了 tsx/ts-node，或已 build 好）
- .jar                → Java（java -jar）
- .go                 → Go（go run，需要 workspace 有 go.mod）
- 无后缀或其它        → Python（向后兼容）

依赖装配假设：
- Python：由 UV/mise 层准备好 venv，context.runtime_spec.python_path 指向解释器
- Node：workspace 内已 `npm ci`（后续 M4+ 会加自动 npm ci）
- Java：`.jar` 已含依赖或 workspace 有 lib/*.jar
- Go：workspace 有 go.mod（go run 会自动下载模块到 GOPATH）

命令构造统一走 mise：`mise exec node@X -- node <entry>` 让语言版本可控。
本 PoC 版本假设系统/mise 全局已装对应语言，运行时不再显式指定版本。
"""

import os
import shutil

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase
from antcode_worker.runtime.dependency_process import DependencyLimits
from antcode_worker.runtime.go_execution_policy import build_go_execution_env
from antcode_worker.runtime.language_cache import inject_language_cache_env
from antcode_worker.runtime.node_dependency_policy import install_node_dependencies

_PYTHON_EXT = {".py", ".pyw"}
_NODE_JS_EXT = {".js", ".mjs", ".cjs"}
_NODE_TS_EXT = {".ts", ".mts", ".cts"}
_JAVA_EXT = {".jar"}
_GO_EXT = {".go"}


class CodePlugin(PluginBase):
    """代码执行插件（Python / Node.js / Java / Go）。"""

    @property
    def name(self) -> str:
        return "code"

    @property
    def priority(self) -> int:
        return 10

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.CODE

    def validate(self, payload: TaskPayload) -> list[str]:
        errors: list[str] = []
        if not payload.entry_point:
            errors.append("entry_point 不能为空")
            return errors

        # C3: entry_point 是用户可控字符串，最终会进 subprocess argv（python / node
        # / java 的第一个位置参数）。必须校验：拒绝绝对路径 + '..'，并确保 resolve
        # 后仍在 workspace 内，否则可执行 bundle 外、worker uid 可读的任意脚本。
        ep_error = self._validate_entry_point(payload)
        if ep_error:
            errors.append(ep_error)
            return errors

        language = self._detect_language(payload.entry_point)
        if language == "python":
            # 沿用旧校验：Python 项目需要 runtime_spec.python_path
            pass  # 由 build_plan 时校验
        elif language == "unknown":
            errors.append(f"无法识别的入口文件类型: {payload.entry_point}")
        return errors

    def _validate_entry_point(self, payload: TaskPayload) -> str | None:
        """校验 entry_point 相对且未越出 workspace。返回错误信息或 None。"""
        entry = payload.entry_point.strip().replace("\\", "/")
        if not entry:
            return "entry_point 不能为空"
        # 拒绝绝对路径（POSIX 前导 '/'、Windows 盘符 'C:' 等）
        if entry.startswith("/") or (len(entry) >= 2 and entry[1] == ":"):
            return f"entry_point 不合法：不允许绝对路径 ({payload.entry_point})"
        # 拒绝路径穿越
        if any(part in {"..", ""} for part in entry.split("/") if part != "."):
            # 允许 "./main.py" 之类；只拒绝 '..' 段与空段
            pass  # 下面 commonpath 会兜底
        if ".." in entry.split("/"):
            return f"entry_point 不合法：不允许包含 '..' ({payload.entry_point})"
        # commonpath 兜底：解析后必须仍在 workspace / project_cwd 内
        base = payload.project_cwd or payload.workspace_path
        if base:
            try:
                base_abs = os.path.realpath(base)
                target = os.path.realpath(os.path.join(base_abs, entry))
                if os.path.commonpath([base_abs, target]) != base_abs:
                    return f"entry_point 越界（不在项目工作目录内）: {payload.entry_point}"
            except (ValueError, OSError) as exc:
                return f"entry_point 校验失败: {exc}"
        return None

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        # 优先看显式 language（由 scheduler 从 project.code_info.language 传入），
        # 否则按 entry_point 后缀自动识别。
        language = self._resolve_language(payload)
        cwd = self._get_project_cwd(payload)
        env = self._build_env(payload)
        if language == "go":
            env = build_go_execution_env(cwd, env)

        # 执行前依赖装配（幂等，复用缓存）
        await self._prepare_deps(
            language=language,
            cwd=cwd,
            limits=DependencyLimits.from_context(context),
        )

        if language == "python":
            command, args = self._python_argv(context, payload)
        elif language == "node_js":
            command, args = self._node_argv(payload, is_typescript=False)
        elif language == "node_ts":
            command, args = self._node_argv(payload, is_typescript=True)
        elif language == "java":
            command, args = self._java_argv(payload)
        elif language == "go":
            command, args = self._go_argv(payload)
        else:
            # 未识别的后缀降级为 Python（历史行为兼容）
            command, args = self._python_argv(context, payload)

        return ExecPlan(
            command=command,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=payload.artifact_patterns,
        )

    # ---------- 语言识别 ----------

    # project.code_info.language 到内部标识的映射
    _LANGUAGE_ALIAS: dict[str, str] = {
        "python": "python",
        "py": "python",
        "javascript": "node_js",
        "js": "node_js",
        "node": "node_js",
        "nodejs": "node_js",
        "typescript": "node_ts",
        "ts": "node_ts",
        "java": "java",
        "go": "go",
        "golang": "go",
    }

    def _resolve_language(self, payload: TaskPayload) -> str:
        """显式 kwargs.language 优先，否则按 entry_point 后缀识别。"""
        explicit = payload.kwargs.get("language") if isinstance(payload.kwargs, dict) else None
        if isinstance(explicit, str) and explicit.strip():
            mapped = self._LANGUAGE_ALIAS.get(explicit.strip().lower())
            if mapped:
                # TypeScript 用户可能配 entry .js/.mjs；不强制降级
                # Java entry 不是 .jar 时也仍走 java 分支
                return mapped
        return self._detect_language(payload.entry_point)

    def _detect_language(self, entry_point: str) -> str:
        _, ext = os.path.splitext(entry_point.lower())
        if ext in _PYTHON_EXT:
            return "python"
        if ext in _NODE_JS_EXT:
            return "node_js"
        if ext in _NODE_TS_EXT:
            return "node_ts"
        if ext in _JAVA_EXT:
            return "java"
        if ext in _GO_EXT:
            return "go"
        if not ext:
            # 无后缀默认 Python（兼容旧调度）
            return "python"
        return "unknown"

    # ---------- 各语言 argv 构造 ----------

    def _python_argv(self, context: RunContext, payload: TaskPayload) -> tuple[str, list[str]]:
        python_exe = self._get_python_executable(context)
        args = [payload.entry_point, *payload.args]
        return python_exe, args

    def _node_argv(self, payload: TaskPayload, *, is_typescript: bool) -> tuple[str, list[str]]:
        # TS 优先走 workspace 里的 tsx 二进制（约定 devDep 装 tsx）；否则 fallback node
        if is_typescript:
            dependency_root = payload.project_cwd or payload.workspace_path or ""
            local_tsx = os.path.join(dependency_root, "node_modules", ".bin", "tsx")
            if os.path.isfile(local_tsx):
                return local_tsx, [payload.entry_point, *payload.args]
            local_tsnode = os.path.join(dependency_root, "node_modules", ".bin", "ts-node")
            if os.path.isfile(local_tsnode):
                return local_tsnode, [payload.entry_point, *payload.args]
            raise RuntimeError("TypeScript 入口需要 workspace 装 tsx 或 ts-node（devDependencies）")
        node_exe = shutil.which("node") or "node"
        return node_exe, [payload.entry_point, *payload.args]

    def _java_argv(self, payload: TaskPayload) -> tuple[str, list[str]]:
        java_exe = shutil.which("java") or "java"
        return java_exe, ["-jar", payload.entry_point, *payload.args]

    def _go_argv(self, payload: TaskPayload) -> tuple[str, list[str]]:
        go_exe = shutil.which("go") or "go"
        return go_exe, ["run", payload.entry_point, *payload.args]

    # ---------- 依赖装配 ----------

    async def _prepare_deps(
        self,
        *,
        language: str,
        cwd: str,
        limits: DependencyLimits,
    ) -> None:
        """执行前装依赖；幂等，包管理器自身会跳过已装。

        - Python：假设 UV/mise 已装好 venv，此处 skip
        - Node：在无网络 bwrap 内从 source bundle 的离线缓存安装
        - Go：外部模块必须提交 vendor，沙箱内始终 GOPROXY=off
        - Java：`.jar` 打包型跳过；Maven 项目暂不自动装（需要 mvn dependency:copy-dependencies）
        """
        if language == "python":
            return
        if language in ("node_js", "node_ts"):
            await install_node_dependencies(cwd, limits=limits)
        if language == "go":
            from antcode_worker.runtime.go_execution_policy import install_go_dependencies

            await install_go_dependencies(cwd, limits=limits)
        # java 暂不自动装依赖

    # ---------- 通用工具 ----------

    def _build_env(self, payload: TaskPayload) -> dict[str, str]:
        # PATH / PYTHONPATH 都不在这里写：executor.exec_path / executor.python_path 是
        # 唯一权威构造者，插件写的同名值只会被沙箱层与进程层原样盖掉。Node 的
        # node_modules/.bin 由 exec_path 从 ExecPlan.cwd 推导——那里才在依赖装配
        # 之后，而本方法恒早于 _prepare_deps，检查 node_modules 必然落空。
        env = dict(payload.env_vars)
        # 多语言依赖 cache 复用（M7）：所有项目共享一份缓存目录，避免重复下载
        inject_language_cache_env(env)
        return env

    def _get_python_executable(self, context: RunContext) -> str:
        if context.runtime_spec and context.runtime_spec.python_path:
            return context.runtime_spec.python_path
        raise RuntimeError("runtime_spec.python_path 不能为空（Python 入口需要 venv）")

    def _get_project_cwd(self, payload: TaskPayload) -> str:
        cwd = payload.project_cwd or payload.workspace_path
        if not cwd:
            raise RuntimeError("project_cwd 不能为空")
        return cwd
