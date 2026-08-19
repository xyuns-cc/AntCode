"""执行语言判据：声明与入口后缀冲突时必须显式拒绝，不许任一方"胜出"。

修复前 CodePlugin 的判据是"显式 language 优先，取不到就按后缀，无后缀降级 Python"，
而 language 根本没进过派发参数，于是实际只剩后缀一条线，且无后缀恒等于 Python。
本文件把三类判据钉死：一致才放行、矛盾必拒、单边信号才可采信。
"""

import pytest
from antcode_contracts.execution_language import (
    ExecutionLanguage,
    ExecutionLanguageError,
    language_from_entry_point,
    normalize_declared_language,
    resolve_execution_language,
)


def test_declared_language_decides_when_entry_point_has_no_suffix():
    """无后缀入口曾恒定降级为 Python，选 Java 的项目因此报 python_path 缺失。"""
    assert resolve_execution_language("java", "app-runner") is ExecutionLanguage.JAVA
    assert resolve_execution_language("go", "cmd/server") is ExecutionLanguage.GO


def test_entry_point_suffix_decides_when_language_is_absent():
    assert resolve_execution_language(None, "main.go") is ExecutionLanguage.GO
    assert resolve_execution_language("", "main.py") is ExecutionLanguage.PYTHON


@pytest.mark.parametrize(
    ("declared", "entry_point"),
    [
        ("python", "server.js"),  # 仓库导入把 language 写死成 python 的典型脏数据
        ("java", "main.py"),
        ("typescript", "app.js"),  # 编译产物与声明不一致，同样不许猜
        ("go", "main.py"),
    ],
)
def test_conflicting_declaration_and_suffix_is_rejected(declared, entry_point):
    with pytest.raises(ExecutionLanguageError, match="请把两者改到一致"):
        resolve_execution_language(declared, entry_point)


def test_agreeing_signals_resolve_to_the_shared_language():
    assert resolve_execution_language("javascript", "app.mjs") is ExecutionLanguage.JAVASCRIPT
    assert resolve_execution_language("TypeScript", "app.ts") is ExecutionLanguage.TYPESCRIPT


def test_unsupported_suffix_is_rejected_even_with_a_declared_language():
    """未知后缀是"明确不受支持"的正向信号，不能退回去按声明值执行。"""
    with pytest.raises(ExecutionLanguageError, match="不属于任何受支持的执行语言"):
        resolve_execution_language("python", "main.rb")


def test_java_entry_must_be_a_jar_because_plugin_only_runs_java_dash_jar():
    with pytest.raises(ExecutionLanguageError, match=r"\.java"):
        language_from_entry_point("Main.java")
    assert language_from_entry_point("app.jar") is ExecutionLanguage.JAVA


def test_no_signal_at_all_is_rejected_instead_of_defaulting_to_python():
    with pytest.raises(ExecutionLanguageError, match="无法确定运行时"):
        resolve_execution_language(None, "runner")


def test_unknown_declared_language_is_rejected_not_ignored():
    """未知声明曾被 dict.get 静默吞掉后落回后缀判定。"""
    with pytest.raises(ExecutionLanguageError, match="不支持的执行语言"):
        normalize_declared_language("klingon")


def test_declared_aliases_normalize_to_the_product_language_names():
    assert normalize_declared_language("nodejs") is ExecutionLanguage.JAVASCRIPT
    assert normalize_declared_language("golang") is ExecutionLanguage.GO
    assert normalize_declared_language(None) is None
