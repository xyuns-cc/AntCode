"""Build the inner process plan for an already wrapped sandbox payload."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from antcode_worker.domain.models import ExecPlan, RuntimeHandle
from antcode_worker.engine.unbound_runtime import WORKER_PYTHON_RUNTIME_HASH
from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.exec_path import authoritative_path
from antcode_worker.executor.process_limits import effective_memory_limit_mb
from antcode_worker.executor.python_path import authoritative_python_path
from antcode_worker.executor.sandbox_cancellation import payload_process_limit
from antcode_worker.executor.sandbox_provider import SandboxProvider
from antcode_worker.executor.sandbox_scratch import private_home

_PAYLOAD_PROCESS_LIMIT_KEY = "payload_max_processes"
_TMPFS_SIZE_KEY = "tmpfs_size_mb"
_HIJACK_ENV_KEYS = ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "BASH_ENV", "ENV")


@dataclass(frozen=True)
class SandboxPlanRequest:
    """Immutable inputs required to derive a sandboxed process plan."""

    exec_plan: ExecPlan
    runtime_handle: RuntimeHandle
    context: dict[str, Any]


def create_sandboxed_plan(
    sandbox: SandboxProvider,
    config: ExecutorConfig,
    request: SandboxPlanRequest,
) -> ExecPlan:
    """Wrap the command, filter its environment, and preserve execution limits."""
    sandbox_context = _sandbox_context(config, request)
    command = sandbox.wrap_command(_payload_command(request), sandbox_context)
    # 沙箱化计划的 cwd 只算一次：PATH 里的 node_modules/.bin 必须按子进程真正的
    # 工作目录推导，与 _copy_execution_contract 写进计划的那个 cwd 是同一个值。
    work_dir = request.context.get("work_dir", request.exec_plan.cwd)
    environment = _sandbox_environment(sandbox, request, sandbox_context, work_dir=work_dir)
    return _copy_execution_contract(request, command=command, environment=environment, work_dir=work_dir)


def _payload_command(request: SandboxPlanRequest) -> list[str]:
    plan = request.exec_plan
    runtime = request.runtime_handle
    command = [runtime.python_executable, plan.command] if plan.command.endswith(".py") else [plan.command]
    return [*command, *plan.args]


def _sandbox_context(config: ExecutorConfig, request: SandboxPlanRequest) -> dict[str, Any]:
    plan = request.exec_plan
    runtime = request.runtime_handle
    return {
        **request.context,
        # rule_egress 把 egress bridge 的 socket / 连接数 / 字节 / 时长四项限额写进
        # plan.sandbox_config，而 rule_network_policy 是从 sandbox context 读它们的。
        # 不并进来，Rule 任务会停在 "Rule egress bridge 缺少有效总时长上限"。
        **plan.sandbox_config,
        _PAYLOAD_PROCESS_LIMIT_KEY: payload_process_limit(plan, config),
        # 内存盘尺寸与 RLIMIT_DATA/RSS 监控共用同一个生效限额，避免两层各解析一遍。
        _TMPFS_SIZE_KEY: effective_memory_limit_mb(plan.memory_limit_mb, config.default_memory_limit_mb),
        "run_id": plan.run_id,
        # Worker 自身解释器（rule 等内建插件）不作为"任务 runtime 目录"挂载：
        # sandbox_mounts 只接受 runtimes_root 下的托管运行时，而 /app/.venv 走的是
        # 另一条通道——sandbox_executables._require_trusted_install_root 已把
        # Worker 安装根列为可信并据此挂载。两者混用会撞上"runtime 范围过宽"。
        # 注意 runtime.path 仍用于下面的 VIRTUAL_ENV/PYTHONPATH，那是环境语义不是挂载。
        "runtime_path": None if runtime.runtime_hash == WORKER_PYTHON_RUNTIME_HASH else runtime.path,
        "runtime_executable": runtime.python_executable,
    }


def _sandbox_environment(
    sandbox: SandboxProvider,
    request: SandboxPlanRequest,
    sandbox_context: dict[str, Any],
    *,
    work_dir: str | None,
) -> dict[str, str]:
    host_environment = os.environ.copy()
    host_environment.update(request.exec_plan.env)
    env_context = {**sandbox_context, "task_env_keys": frozenset(request.exec_plan.env)}
    environment = sandbox.filter_env(host_environment, env_context)
    runtime_path = request.runtime_handle.path
    environment.update(
        # 与 ProcessExecutor._build_env 共用同一构造函数：进程层是最终权威，
        # 两处各写一份会像历史缺陷那样让长值被短值静默盖掉。
        PYTHONPATH=authoritative_python_path(request.exec_plan, runtime_path),
        VIRTUAL_ENV=runtime_path,
        HOME=private_home(),
        PATH=authoritative_path(work_dir, runtime_path),
    )
    for key in _HIJACK_ENV_KEYS:
        environment.pop(key, None)
    return environment


def _copy_execution_contract(
    request: SandboxPlanRequest,
    *,
    command: list[str],
    environment: dict[str, str],
    work_dir: str | None,
) -> ExecPlan:
    plan = request.exec_plan
    return ExecPlan(
        command=command[0],
        args=command[1:],
        env=environment,
        cwd=work_dir,
        workspace_root=plan.workspace_root,
        run_id=plan.run_id,
        timeout_seconds=plan.timeout_seconds,
        grace_period_seconds=plan.grace_period_seconds,
        memory_limit_mb=plan.memory_limit_mb,
        cpu_limit_seconds=plan.cpu_limit_seconds,
        max_open_files=plan.max_open_files,
        max_processes=plan.max_processes,
        max_file_size_mb=plan.max_file_size_mb,
        enforce_rlimit=plan.enforce_rlimit,
        artifact_patterns=plan.artifact_patterns,
        collect_stdout=plan.collect_stdout,
        collect_stderr=plan.collect_stderr,
        sandbox_enabled=False,
        sandbox_config=dict(plan.sandbox_config),
        plugin_name=plan.plugin_name,
    )


__all__ = ["SandboxPlanRequest", "create_sandboxed_plan"]
