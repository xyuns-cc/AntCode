"""派发前的任务帧装配：把项目侧派生的信息盖进即将下发的任务字典。

这里是 run 级 source bundle 信息（entry_point / bundle 摘要 / 执行语言）唯一进入
任务帧的地方。``params.kwargs.language`` 因此只有 Master 一个写入方，且每次派发都
无条件覆盖——任务自己 execution_params 里带的同名键不会生效。执行语言是项目级配置，
不能由单次任务参数分叉出第二个真源（Worker 侧 CodePlugin 是唯一读取方）。
"""

from __future__ import annotations

from typing import Any

RUNTIME_ENV_KEY = "ANTCODE_RUNTIME_ENV"
LANGUAGE_PARAM_KEY = "language"


def with_dispatch_language(params: Any, language: Any) -> dict[str, Any]:
    """返回覆盖了 kwargs.language 的新 params，不修改传入对象。"""
    if not isinstance(language, str) or not language.strip():
        raise ValueError("派发信息缺少执行语言，拒绝派发")
    base = dict(params) if isinstance(params, dict) else {}
    kwargs = base.get("kwargs")
    merged = dict(kwargs) if isinstance(kwargs, dict) else {}
    merged[LANGUAGE_PARAM_KEY] = language.strip()
    base["kwargs"] = merged
    return base


def enrich_dispatch_tasks(tasks: list[dict], run_download_info: dict[str, dict]) -> list[dict]:
    """按 run_id 把 source bundle 派发信息合并进任务副本。"""
    return [_enrich_one(task, run_download_info.get(_run_id(task))) for task in tasks]


def _run_id(task: dict) -> str:
    return str(task.get("run_id") or task.get("task_id") or "")


def _enrich_one(task: dict, info: dict | None) -> dict:
    task_copy = dict(task)
    # 保留键只能通过可信顶层字段派发，不能由普通子进程环境决定 runtime。
    environment = dict(task_copy.get("environment") or {})
    environment.pop(RUNTIME_ENV_KEY, None)
    task_copy["environment"] = environment
    if info is None:
        return task_copy
    task_copy["source_bundle_uri"] = info.get("source_bundle_uri", "")
    task_copy["source_bundle_sha256"] = info.get("source_bundle_sha256", "")
    task_copy["source_bundle_size"] = info.get("source_bundle_size", 0)
    task_copy["source_subdir"] = info.get("source_subdir", "")
    task_copy["transfer_method"] = info.get("transfer_method", "source_bundle")
    task_copy["entry_point"] = info.get("entry_point") or task.get("entry_point", "")
    task_copy["resolved_revision"] = info.get("resolved_revision", "")
    task_copy["params"] = with_dispatch_language(task_copy.get("params"), info.get("language"))
    return task_copy


__all__ = [
    "LANGUAGE_PARAM_KEY",
    "RUNTIME_ENV_KEY",
    "enrich_dispatch_tasks",
    "with_dispatch_language",
]
