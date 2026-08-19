"""TaskRun "尚未结算" 的判据只允许有一个定义点。

历史形状：``worker_delete_guard.ACTIVE_RUN_STATUSES``（裸字符串）与
``retry_dispatch_recovery.ACTIVE_RUN_STATUSES``（枚举）各写一份，成员今天相同、
明天不一定——本仓已经吃过"改错同名副本"的亏。收敛后唯一定义点是
``task_status_sets.TASK_RUN_ACTIVE_STATUSES``，由终态取补派生。

这里钉三件事：

1. 终态 / 活跃构成 TaskStatus 的一个划分（互斥且穷尽），"取补"才立得住；
   谁把派生式改回手写字面量，这条先红。
2. 全部**阻塞判据**调用点解析到同一个对象，而不是"长得一样"的副本。
3. 谁再手写一份覆盖派发四态的集合，必须在 ``PERMISSION_PREDICATES`` 里
   写明它为什么不是重复——那一族是**许可判据**，多算一个状态等于多放行一次
   （fail-open），与阻塞判据（多算 = 多拦一次，fail-closed）安全方向相反，
   不能合并、更不能改成取补派生。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from antcode_core.domain.models.enums import TaskStatus
from antcode_core.domain.models.task_status_sets import (
    SPIDER_WRITABLE_TASK_STATUSES,
    TASK_RUN_ACTIVE_STATUSES,
    TASK_RUN_TERMINAL_STATUSES,
)

SOURCE_ROOTS = (Path("packages"), Path("services"))

# 派发四态：一条 run 被调度面接管、尚未拿到结果的全部阶段。任何手写集合只要
# 覆盖它们，就与"尚未结算"高度重叠，必须显式声明自己属于哪一族。
DISPATCH_PHASE_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.DISPATCHING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
    }
)

# 引用唯一定义点的阻塞判据调用点：删除守卫、Worker 级联删除复查、项目删除
# 作用域、并发实例计数、取消悬挂收敛、一次性调度兑现、临时 Worker 清理。
BLOCKING_PREDICATE_MODULES = (
    "antcode_core.application.services.workers.worker_delete_guard",
    "antcode_core.application.services.workers.worker_service",
    "antcode_core.application.services.projects.project_delete_scope",
    "antcode_master.control.retry_dispatch_recovery",
    "antcode_master.control.cancel_settlement",
    "antcode_master.control.durable_schedule",
    "antcode_master.control.provisional_worker_cleanup",
)

# 收敛前用过的名字。留在任何模块里都意味着副本回来了。
RETIRED_ALIAS = "ACTIVE_RUN_STATUSES"

# 允许与活跃集合重叠的许可判据。每一条回答的都是"允许这条 run 继续做某件事"，
# 而不是"这条 run 还会不会被推进"；新增条目必须在这里写明理由。
PERMISSION_PREDICATES = {
    # 允许 Spider 继续写入采集数据。
    ("packages/antcode_core/src/antcode_core/domain/models/task_status_sets.py", "SPIDER_WRITABLE_TASK_STATUSES"),
    # 允许把 run 认领为自己的执行归属（ownership claim）。
    (
        "packages/antcode_core/src/antcode_core/application/services/workers/run_ownership_service.py",
        "_TASK_CLAIMABLE_STATUSES",
    ),
    # 允许为 run 记录一次用户取消请求。
    (
        "packages/antcode_core/src/antcode_core/application/services/scheduler/cancel_request_service.py",
        "_CANCEL_REQUESTABLE_STATUSES",
    ),
    # 允许用户对任务的最新 run 发起取消。
    ("services/web_api/src/antcode_web_api/routes/v1/task_cancel.py", "CANCELLABLE_TASK_STATUSES"),
    # 允许用户对单条 run 发起取消。
    ("services/web_api/src/antcode_web_api/routes/v1/runs.py", "_CANCELLABLE_STATUSES"),
}


def test_terminal_and_active_partition_every_task_status() -> None:
    """取补的前提：两族互斥且合起来是 TaskStatus 全集。"""
    assert TASK_RUN_TERMINAL_STATUSES.isdisjoint(TASK_RUN_ACTIVE_STATUSES)
    assert TASK_RUN_TERMINAL_STATUSES | TASK_RUN_ACTIVE_STATUSES == frozenset(TaskStatus)
    # 派生而非手写的可观察后果：PAUSED 不在终态里，就必然被算作"仍活跃"。
    # 收敛前两份手写副本都漏了它。
    assert TaskStatus.PAUSED in TASK_RUN_ACTIVE_STATUSES


def test_permission_predicates_stay_narrower_than_the_blocking_predicate() -> None:
    """许可判据必须是活跃集合的真子集：多放行一个状态就是多授一次权。"""
    writable = frozenset(SPIDER_WRITABLE_TASK_STATUSES)
    assert writable < TASK_RUN_ACTIVE_STATUSES


def test_every_blocking_call_site_resolves_to_the_single_definition() -> None:
    for module_name in BLOCKING_PREDICATE_MODULES:
        module = importlib.import_module(module_name)
        resolved = getattr(module, "TASK_RUN_ACTIVE_STATUSES", None)
        assert resolved is TASK_RUN_ACTIVE_STATUSES, f"{module_name} 未使用唯一定义点"
        assert not hasattr(module, RETIRED_ALIAS), f"{module_name} 重新引入了 {RETIRED_ALIAS} 副本"


def _literal_statuses(node: ast.expr) -> frozenset[TaskStatus] | None:
    """把字面量集合折算成 TaskStatus；含非字面量成员则放弃判定，返回 None。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1:
        return _literal_statuses(node.args[0]) if node.func.id in {"frozenset", "set", "tuple", "list"} else None
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    members = [_status_of(element) for element in node.elts]
    return None if any(member is None for member in members) else frozenset(members)  # type: ignore[arg-type]


def _status_of(element: ast.expr) -> TaskStatus | None:
    if isinstance(element, ast.Constant) and isinstance(element.value, str):
        return TaskStatus(element.value) if element.value in set(TaskStatus) else None
    if isinstance(element, ast.Attribute) and isinstance(element.value, ast.Name):
        if element.value.id != "TaskStatus":
            return None
        return TaskStatus.__members__.get(element.attr)
    return None


def _module_level_status_sets(path: Path) -> list[tuple[str, frozenset[TaskStatus]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, frozenset[TaskStatus]]] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        statuses = _literal_statuses(node.value)
        if statuses is None:
            continue
        found.extend((target.id, statuses) for target in node.targets if isinstance(target, ast.Name))
    return found


def test_no_unclassified_copy_of_the_active_status_set() -> None:
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            for name, statuses in _module_level_status_sets(path):
                if not statuses >= DISPATCH_PHASE_STATUSES:
                    continue
                if (path.as_posix(), name) in PERMISSION_PREDICATES:
                    continue
                offenders.append(f"{path.as_posix()}::{name}")
    assert not offenders, (
        "手写集合覆盖了派发四态，与 TASK_RUN_ACTIVE_STATUSES 重复："
        + "、".join(offenders)
        + "。阻塞判据请直接引用唯一定义点；确属许可判据请登记到 PERMISSION_PREDICATES 并写明理由。"
    )
