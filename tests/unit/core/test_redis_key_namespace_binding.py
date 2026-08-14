"""core 侧 ``RedisKeys`` 必须显式绑定 namespace。

仓库里有两个同名 ``RedisKeys``：

* ``antcode_worker.transport.redis.keys.RedisKeys``
  ``DEFAULT_NAMESPACE = redis_namespace()`` —— 裸构造会跟随 ``REDIS_NAMESPACE``。
* ``antcode_core.infrastructure.redis.keys.RedisKeys``
  ``DEFAULT_NAMESPACE = "antcode"`` —— **硬编码字面量**，裸构造在
  ``REDIS_NAMESPACE`` 非默认时会生成一组没人写的 key，读到空数据且不报错。

两者签名一致、名字一致，改错副本的成本极高。这里对 core 版本的所有生产调用点
做结构性断言：不许裸构造。写死一次具体调用点没有意义——被绕开只是时间问题。
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_KEYS_MODULES = (
    "antcode_core.infrastructure.redis.keys",
    "antcode_core.infrastructure.redis",
)
SOURCE_ROOTS = (Path("packages"), Path("services"))


def _imports_core_redis_keys(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in CORE_KEYS_MODULES:
            continue
        if any(alias.name == "RedisKeys" for alias in node.names):
            return True
    return False


def _bare_constructions(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RedisKeys"
        and not node.args
        and not node.keywords
    ]


def _production_sources() -> list[Path]:
    return [
        path
        for root in SOURCE_ROOTS
        for path in root.rglob("*.py")
        if "/tests/" not in path.as_posix() and not path.name.startswith("test_")
    ]


def test_core_redis_keys_is_never_constructed_without_a_namespace() -> None:
    offenders: list[str] = []
    inspected = 0
    for path in _production_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _imports_core_redis_keys(tree):
            continue
        inspected += 1
        offenders.extend(f"{path}:{lineno}" for lineno in _bare_constructions(tree))

    assert inspected, "没有扫到任何导入 core RedisKeys 的生产模块，断言已失效"
    assert not offenders, (
        "core RedisKeys 必须显式传 namespace（DEFAULT_NAMESPACE 是硬编码 'antcode'，"
        f"不跟随 REDIS_NAMESPACE）: {offenders}"
    )


def test_core_redis_keys_default_is_a_literal_not_derived_from_env() -> None:
    """上面那条规则的前提本身也要钉住。

    哪天 core 侧改成跟随 ``redis_namespace()``，这条会失败，提醒把规则一并撤掉，
    而不是留一条无人理解的历史约束。
    """
    from antcode_core.infrastructure.redis.keys import RedisKeys as CoreRedisKeys

    core_source = Path("packages/antcode_core/src/antcode_core/infrastructure/redis/keys.py").read_text(
        encoding="utf-8"
    )
    worker_source = Path("services/worker/src/antcode_worker/transport/redis/keys.py").read_text(encoding="utf-8")

    assert CoreRedisKeys.DEFAULT_NAMESPACE == "antcode"
    assert "redis_namespace" not in core_source, "core RedisKeys 已跟随 REDIS_NAMESPACE，请撤掉本模块的裸构造禁令"
    assert "DEFAULT_NAMESPACE: ClassVar[str] = redis_namespace()" in worker_source
    assert CoreRedisKeys("tenant-a").spider_data_stream("run-1") != CoreRedisKeys().spider_data_stream("run-1")
