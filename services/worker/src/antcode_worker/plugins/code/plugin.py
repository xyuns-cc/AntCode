"""代码执行插件（多语言 + 依赖自动装配）。

语言判定不在这里做，统一走 ``antcode_contracts.execution_language``：项目声明的
language（Master 随 ``params.kwargs.language`` 下发）与 entry_point 后缀两个信号
合并，矛盾即拒绝执行。这里只负责把判定结果翻译成 argv：

- python      → runtime_spec.python_path（uv/mise venv 的解释器）
- javascript  → 镜像预装的 node
- typescript  → 镜像预装的 node + workspace devDep 里的 tsx / ts-node
- java        → java -jar
- go          → 装配期 `go build` 出的产物本身（不是 `go run`，见 go_execution_policy）

依赖装配：
- Python：由 UV/mise 层准备好 venv，context.runtime_spec.python_path 指向解释器
- Node：装配期按 lockfile 离线安装（``node_dependency_policy.install_node_dependencies``）
- Java：`.jar` 已含依赖或 workspace 有 lib/*.jar
- Go：外部模块必须随源码提交 vendor，装配期在无网沙箱内编译

Node/Go/Java 的语言二进制由镜像构建期预装并写进 PATH（见
``infra/docker/Dockerfile.worker`` 与 ``runtime.language_runtime``），这里只解析
绝对路径；缺失直接抛错，不退化成裸命令名，也不退化成别的执行方式。
"""

import os

from antcode_contracts.execution_language import (
    ExecutionLanguage,
    ExecutionLanguageError,
    resolve_execution_language,
)

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase
from antcode_worker.runtime.dependency_process import DependencyLimits
from antcode_worker.runtime.go_execution_policy import (
    build_go_binary,
    build_go_execution_env,
    go_binary_path,
    install_go_dependencies,
)
from antcode_worker.runtime.language_cache import inject_language_cache_env
from antcode_worker.runtime.language_runtime import (
    JAVA_RUNTIME,
    NODE_RUNTIME,
    require_language_executable,
)
from antcode_worker.runtime.node_dependency_errors import (
    NODE_DEP_TS_RUNNER_MISSING,
    NodeDependencyInstallError,
)
from antcode_worker.runtime.node_dependency_policy import install_node_dependencies
from antcode_worker.runtime.runtime_budget import (
    RuntimeBudget,
    java_runtime_argv,
    node_runtime_argv,
    resolve_runtime_budget,
)

# 顺序即优先级：tsx 是当前主流 runner，ts-node 作为老项目的既有约定保留。
_TYPESCRIPT_RUNNERS = ("tsx", "ts-node")
_NODE_LANGUAGES = (ExecutionLanguage.JAVASCRIPT, ExecutionLanguage.TYPESCRIPT)


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

        try:
            self._resolve_language(payload)
        except ExecutionLanguageError as exc:
            errors.append(str(exc))
        return errors

    def _validate_entry_point(self, payload: TaskPayload) -> str | None:
        """校验 entry_point 相对且未越出 workspace。返回错误信息或 None。"""
        entry = payload.entry_point.strip().replace("\\", "/")
        if not entry:
            return "entry_point 不能为空"
        # 拒绝绝对路径（POSIX 前导 '/'、Windows 盘符 'C:' 等）
        if entry.startswith("/") or (len(entry) >= 2 and entry[1] == ":"):
            return f"entry_point 不合法：不允许绝对路径 ({payload.entry_point})"
        # 拒绝路径穿越（"./main.py" 之类的单点段合法，只拒 '..'；越界由下面 commonpath 兜底）
        if ".." in entry.split("/"):
            return f"entry_point 不合法：不允许包含 '..' ({payload.entry_point})"
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
        language = self._resolve_language(payload)
        cwd = self._get_project_cwd(payload)
        # 沙箱内的运行时看不见 cgroup、只看得见宿主 /proc，必须由这里把生效限额喂给它们
        budget = resolve_runtime_budget(context.memory_limit_mb)
        env = self._build_env(payload)
        if language is ExecutionLanguage.GO:
            env = build_go_execution_env(cwd, env, budget)

        # 执行前依赖装配（幂等，复用缓存）；Go 的编译也在这里，_build_argv 依赖它的产物
        await self._prepare_deps(
            language=language,
            payload=payload,
            cwd=cwd,
            limits=DependencyLimits.from_context(context),
            budget=budget,
        )
        command, args = self._build_argv(language, context, payload, budget=budget)

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

    @staticmethod
    def _resolve_language(payload: TaskPayload) -> ExecutionLanguage:
        """合并 Master 下发的 language 与快照 entry_point；矛盾即 ExecutionLanguageError。

        两者来源不同步是设计内的：entry_point 取自 run 首次派发时冻结的源码快照，
        language 取自派发那一刻的项目详情。用户改了入口却没改语言（或反之）时，
        这里必须让 run 显式失败，而不是替他挑一个。
        """
        kwargs = payload.kwargs if isinstance(payload.kwargs, dict) else {}
        return resolve_execution_language(kwargs.get("language"), payload.entry_point)

    # ---------- 各语言 argv 构造 ----------

    def _build_argv(
        self,
        language: ExecutionLanguage,
        context: RunContext,
        payload: TaskPayload,
        *,
        budget: RuntimeBudget,
    ) -> tuple[str, list[str]]:
        if language is ExecutionLanguage.PYTHON:
            return self._python_argv(context, payload)
        if language in _NODE_LANGUAGES:
            is_typescript = language is ExecutionLanguage.TYPESCRIPT
            return self._node_argv(payload, budget, is_typescript=is_typescript)
        if language is ExecutionLanguage.JAVA:
            return self._java_argv(payload, budget)
        if language is ExecutionLanguage.GO:
            return self._go_argv(payload)
        raise RuntimeError(f"CodePlugin 没有 {language.value} 的执行方式实现")

    def _python_argv(self, context: RunContext, payload: TaskPayload) -> tuple[str, list[str]]:
        python_exe = self._get_python_executable(context)
        args = [payload.entry_point, *payload.args]
        return python_exe, args

    def _node_argv(
        self,
        payload: TaskPayload,
        budget: RuntimeBudget,
        *,
        is_typescript: bool,
    ) -> tuple[str, list[str]]:
        if is_typescript:
            return self._typescript_argv(payload, budget)
        node_exe = require_language_executable(NODE_RUNTIME)
        return node_exe, [*node_runtime_argv(budget), payload.entry_point, *payload.args]

    def _typescript_argv(self, payload: TaskPayload, budget: RuntimeBudget) -> tuple[str, list[str]]:
        """TS 入口由镜像的 node 去跑 workspace 自带的 TS runner（约定 devDep 装 tsx 或 ts-node）。

        argv[0] 必须是 node 而不是 ``node_modules/.bin/<runner>``：沙箱是从 payload
        可执行文件反推要挂载的安装根的（``sandbox_executables.executable_mount_roots``），
        而 runner 位于 work_dir 内，会被判定成"已经可见"直接跳过，于是镜像里 node 的
        mise 安装根一个都不进 namespace。runner 的 ``#!/usr/bin/env node`` shebang
        随即在沙箱内解析失败，任务以 ``env: 'node': No such file or directory``（127）
        收场——又一次把交付缺陷伪装成任务自身的错误。把 node 摆到 argv[0]，它的安装根
        才会被挂进去，runner 自身派生的 node 子进程也才有 PATH 可用。
        """
        dependency_root = payload.project_cwd or payload.workspace_path or ""
        for runner in _TYPESCRIPT_RUNNERS:
            local_runner = os.path.join(dependency_root, "node_modules", ".bin", runner)
            if os.path.isfile(local_runner):
                node_exe = require_language_executable(NODE_RUNTIME)
                # V8 参数必须排在脚本路径之前，落到 runner 之后就成了 runner 的位置参数
                return node_exe, [*node_runtime_argv(budget), local_runner, payload.entry_point, *payload.args]
        # 只说"需要装 tsx"会把人引向"把 node_modules 提交进仓库"这条死路：
        # source bundle 不收符号链接，而 .bin/<runner> 永远是符号链接。
        raise NodeDependencyInstallError(
            NODE_DEP_TS_RUNNER_MISSING,
            f"TypeScript 入口在 {dependency_root or '<workspace>'} 下找不到 "
            f"node_modules/.bin/{{{','.join(_TYPESCRIPT_RUNNERS)}}}；"
            "正确做法是把 tsx 或 ts-node 写进 devDependencies，并把 package-lock.json 与 "
            ".antcode-deps/npm-cache/ 一起提交，由 Worker 在无网络沙箱内 npm ci 装出来。"
            "直接提交 node_modules/ 不可行：source bundle 不接受符号链接",
        )

    def _java_argv(self, payload: TaskPayload, budget: RuntimeBudget) -> tuple[str, list[str]]:
        java_exe = require_language_executable(JAVA_RUNTIME)
        # VM 参数必须排在 -jar 之前，落到 -jar 之后就成了被执行程序的 argv
        return java_exe, [*java_runtime_argv(budget), "-jar", payload.entry_point, *payload.args]

    def _go_argv(self, payload: TaskPayload) -> tuple[str, list[str]]:
        """argv[0] 是装配期编译出的产物，不是 ``go``。

        ``go run`` 会把被编译程序的退出码折成自己的 1（真值只在 stderr 的
        ``exit status N``），直接 exec 产物才能把退出码原样交回给 Worker。
        副作用是任务沙箱里不再需要 Go 工具链——argv[0] 落在 work_dir 内，
        ``sandbox_executables`` 因此不会把 mise 的 go 安装根挂进 namespace。
        """
        return str(go_binary_path(self._get_project_cwd(payload))), [*payload.args]

    # ---------- 依赖装配 ----------

    async def _prepare_deps(
        self,
        *,
        language: ExecutionLanguage,
        payload: TaskPayload,
        cwd: str,
        limits: DependencyLimits,
        budget: RuntimeBudget,
    ) -> None:
        """执行前装依赖；幂等，包管理器自身会跳过已装。

        - Python：假设 UV/mise 已装好 venv，此处 skip
        - Node：在无网络 bwrap 内从 source bundle 的离线缓存安装
        - Go：外部模块必须提交 vendor，随后在同一个无网沙箱内编译出可执行产物
        - Java：`.jar` 打包型跳过；Maven 项目暂不自动装（需要 mvn dependency:copy-dependencies）
        """
        if language in _NODE_LANGUAGES:
            await install_node_dependencies(cwd, limits=limits)
            return
        if language is ExecutionLanguage.GO:
            await install_go_dependencies(cwd, limits=limits)
            await build_go_binary(cwd, entry_point=payload.entry_point, limits=limits, budget=budget)
        # python / java 无需在此装依赖

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
