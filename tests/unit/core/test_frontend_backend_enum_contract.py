"""前端字面量联合类型 ↔ 后端枚举的结构性绑定。

两侧各自声明同一套取值、没有任何可执行的一致性检查，是本轮多起契约漂移的共同根因
（`TaskStatus.REJECTED` 两个渲染器都没有 case；`formatTaskType` 把 TaskTriggerType
的取值当成 TaskType；`LogMessageType` 少了 system/application，用户一收窄日志类型
过滤器就永久丢行）。

分工：本文件锁"后端枚举 == 前端联合类型"；前端那侧由 TypeScript 的
`Record<联合类型, …>` 强制"联合类型 == 所有展示映射"（漏一个 case 即编译失败）。
两段接起来，后端加一个枚举值而前端没跟上时，门禁必定有一处红。
"""

import re
from pathlib import Path

from antcode_core.domain.models.enums import ExecutionStrategy, ScheduleType, TaskStatus, TaskType
from antcode_core.domain.schemas.logs import LogLevel, LogType

FRONTEND_SRC = Path("web/antcode-frontend/src")
TASK_TYPES = FRONTEND_SRC / "types/task.ts"
PROJECT_MODELS = FRONTEND_SRC / "types/projectModels.ts"
LOG_SERVICE = FRONTEND_SRC / "services/logs.ts"
LOG_VIEWER_TYPES = FRONTEND_SRC / "components/ui/LogViewer/enhancedLogViewerTypes.ts"
STRING_UNION_MEMBER = re.compile(r"'([^']*)'")

BOUND_ENUMS = (
    (TASK_TYPES, "TaskStatus", TaskStatus),
    (TASK_TYPES, "TaskType", TaskType),
    (TASK_TYPES, "ScheduleType", ScheduleType),
    (PROJECT_MODELS, "ExecutionStrategy", ExecutionStrategy),
    (LOG_SERVICE, "LogType", LogType),
    (LOG_SERVICE, "LogLevel", LogLevel),
)


def _string_union(source: Path, alias: str) -> set[str]:
    """读取 `export type <alias> = 'a' | 'b'`（单行或多行）的全部字面量成员。"""
    declaration = re.search(
        rf"export type {alias} =((?:\s*\|?\s*'[^']*')+)",
        source.read_text(encoding="utf-8"),
    )
    assert declaration is not None, f"{source} 未声明字面量联合类型 {alias}"
    return set(STRING_UNION_MEMBER.findall(declaration.group(1)))


def test_frontend_string_unions_match_the_backend_enums() -> None:
    for source, alias, enum in BOUND_ENUMS:
        assert _string_union(source, alias) == {member.value for member in enum}, f"{source}:{alias}"


def test_log_viewer_message_type_is_derived_from_the_backend_log_type() -> None:
    """日志查看器的类型集合必须是后端 LogType 的超集，且靠类型系统而不是人工同步。

    LogMessageType 自己再抄一遍 'stdout' | 'stderr' 时，后端已经在发的 system /
    application 会被 DEFAULT_TYPES 漏掉：filterLogMessages 只要判定"过滤生效"就把
    未列出的类型整行丢弃，而过滤器里没有能把它们选回来的选项——丢行不可恢复。
    """
    source = LOG_VIEWER_TYPES.read_text(encoding="utf-8")

    assert "import type { LogType } from '@/services/logs'" in source
    assert re.search(r"export type LogMessageType = LogType\s*\|", source) is not None
    assert "export const LOG_TYPE_LABELS: Record<LogType, string>" in source
    assert "export const DEFAULT_TYPES = Object.keys(LOG_TYPE_LABELS)" in source
    assert not re.search(r"export const DEFAULT_TYPES = \[", source)
