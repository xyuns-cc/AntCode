"""执行语言契约：项目声明的 language 与入口文件后缀之间的唯一判据。

Master 与 Worker 各自都要回答"这个项目该用哪个运行时执行"：Master 在派发准备
阶段拿到的是项目详情行上的 ``language`` + ``entry_point``，Worker 在构建执行计划
时拿到的是随派发下来的 ``language`` 加上不可变源码快照里的 ``entry_point``。两边
的算法必须逐字一致，否则又是一组同名双实现——所以判据只在这里写一次。

冲突判据是**显式拒绝**，不是任意一方胜出：

- 声明与后缀都能定语言但不一致：两个信号里必有一个是错的，而无从判断是哪一个
  （``language=python`` + ``main.js`` 既可能是导入时把 language 写死成了 python，
  也可能是用户改了入口忘了改语言）。任选一方都会把用户代码交给错误的解释器，
  报出与真实原因毫无关系的语法错误。
- 后缀不认识（``.rb`` / ``.rs``）：这是"明确不属于受支持语言"的正向信号，不是
  信息缺失，不允许退回去按声明值执行。
- 只有一侧有信号（入口无扩展名，或项目没写 language）：这不是分歧，用现存的那个
  信号；缺失的一侧不构成冲突。
- 两侧都没有信号：没有任何依据，拒绝。

内部标识直接采用产品对外承诺的五个语言名（见前端 ``LANGUAGE_OPTIONS``），避免
再引入一套 ``node_js`` / ``node_ts`` 之类的第二套词汇。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath

__all__ = [
    "SUPPORTED_DECLARED_LANGUAGES",
    "ExecutionLanguage",
    "ExecutionLanguageError",
    "language_from_entry_point",
    "normalize_declared_language",
    "resolve_execution_language",
]


class ExecutionLanguage(StrEnum):
    """产品对外承诺、且 Worker 镜像真正预装了运行时的执行语言。"""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"


class ExecutionLanguageError(ValueError):
    """执行语言无法确定，或声明与入口文件互相矛盾。"""


# 历史数据与外部调用方写过的别名。只做同义词归一，不做"猜一个最像的"。
_DECLARED_ALIASES: dict[str, ExecutionLanguage] = {
    "python": ExecutionLanguage.PYTHON,
    "py": ExecutionLanguage.PYTHON,
    "javascript": ExecutionLanguage.JAVASCRIPT,
    "js": ExecutionLanguage.JAVASCRIPT,
    "node": ExecutionLanguage.JAVASCRIPT,
    "nodejs": ExecutionLanguage.JAVASCRIPT,
    "typescript": ExecutionLanguage.TYPESCRIPT,
    "ts": ExecutionLanguage.TYPESCRIPT,
    "java": ExecutionLanguage.JAVA,
    "go": ExecutionLanguage.GO,
    "golang": ExecutionLanguage.GO,
}

# Java 只收 ``.jar``：CodePlugin 的 Java 形态只有 ``java -jar``，没有任何编译路径，
# 收下 ``.java`` 只会在沙箱里以 "找不到主类" 收场。
_ENTRY_POINT_SUFFIXES: dict[str, ExecutionLanguage] = {
    ".py": ExecutionLanguage.PYTHON,
    ".pyw": ExecutionLanguage.PYTHON,
    ".js": ExecutionLanguage.JAVASCRIPT,
    ".mjs": ExecutionLanguage.JAVASCRIPT,
    ".cjs": ExecutionLanguage.JAVASCRIPT,
    ".ts": ExecutionLanguage.TYPESCRIPT,
    ".mts": ExecutionLanguage.TYPESCRIPT,
    ".cts": ExecutionLanguage.TYPESCRIPT,
    ".jar": ExecutionLanguage.JAVA,
    ".go": ExecutionLanguage.GO,
}

SUPPORTED_DECLARED_LANGUAGES: tuple[str, ...] = tuple(language.value for language in ExecutionLanguage)


def normalize_declared_language(value: object) -> ExecutionLanguage | None:
    """把项目声明的 language 归一到枚举；未声明返回 None，声明了但不认识则报错。"""
    if value is None:
        return None
    if isinstance(value, ExecutionLanguage):
        return value
    if not isinstance(value, str):
        raise ExecutionLanguageError(f"执行语言必须是字符串，收到 {type(value).__name__}")
    raw = value.strip().lower()
    if not raw:
        return None
    resolved = _DECLARED_ALIASES.get(raw)
    if resolved is None:
        supported = "/".join(SUPPORTED_DECLARED_LANGUAGES)
        raise ExecutionLanguageError(f"不支持的执行语言 {value!r}，只支持 {supported}")
    return resolved


def language_from_entry_point(entry_point: object) -> ExecutionLanguage | None:
    """从入口文件后缀推导语言；无扩展名返回 None，扩展名不受支持则报错。"""
    if not isinstance(entry_point, str):
        raise ExecutionLanguageError(f"入口文件必须是字符串，收到 {type(entry_point).__name__}")
    suffix = PurePosixPath(entry_point.strip().replace("\\", "/")).suffix.lower()
    if not suffix:
        return None
    resolved = _ENTRY_POINT_SUFFIXES.get(suffix)
    if resolved is None:
        raise ExecutionLanguageError(f"入口文件 {entry_point!r} 的后缀 {suffix} 不属于任何受支持的执行语言")
    return resolved


def resolve_execution_language(declared: object, entry_point: object) -> ExecutionLanguage:
    """按上述判据合并两个信号；无法确定或互相矛盾时抛 ExecutionLanguageError。"""
    declared_language = normalize_declared_language(declared)
    derived_language = language_from_entry_point(entry_point)
    if declared_language is not None and derived_language is not None:
        if declared_language is not derived_language:
            raise ExecutionLanguageError(
                f"项目声明的执行语言是 {declared_language.value}，入口文件 {entry_point!r} "
                f"指向的却是 {derived_language.value}；请把两者改到一致"
            )
        return derived_language
    resolved = declared_language or derived_language
    if resolved is None:
        raise ExecutionLanguageError(
            f"入口文件 {entry_point!r} 没有可识别的后缀，项目也没有声明执行语言，无法确定运行时"
        )
    return resolved
