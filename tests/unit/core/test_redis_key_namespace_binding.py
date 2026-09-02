"""core 侧 ``RedisKeys`` 必须显式绑定 namespace。

仓库里有两个同名 ``RedisKeys``：

* ``antcode_worker.transport.redis.keys.RedisKeys``
  ``DEFAULT_NAMESPACE = redis_namespace()`` —— 裸构造会跟随 ``REDIS_NAMESPACE``。
* ``antcode_core.infrastructure.redis.keys.RedisKeys``
  ``DEFAULT_NAMESPACE = "antcode"`` —— **硬编码字面量**，裸构造在
  ``REDIS_NAMESPACE`` 非默认时会生成一组没人写的 key，读到空数据且不报错。

两者签名一致、名字一致，改错副本的成本极高。这里对 core 版本的所有生产调用点
做结构性断言：不许裸构造。写死一次具体调用点没有意义——被绕开只是时间问题。

八个 ``spider_*``：**判定不收敛**，理由记在这里
--------------------------------------------------

两侧的八个 ``spider_*`` 输出逐字节相同，且两边都活：Worker 侧写
（``plugins/spider/data/reader.py`` / ``reporter.py``），Gateway 与 web_api 侧读
（``handlers/spider_data.py``、``workers_direct_spider.py``、``crawl_item_stream.py``、
``run_spider_items.py``）。写方和读方分居两个类，格式漂移的后果是静默空读——所以本
模块加了逐字节交叉断言，把漂移变成会红的。**但不合并**：

1. 正确的收敛形状是把八个格式提到 ``control_plane`` 做模块级函数（Worker 侧的
   ``task_ready_stream`` / ``log_stream`` / ``heartbeat_key`` 等八个方法已经是这个
   形状：本地类只留 namespace 绑定，格式归共享函数）。那要同时改
   ``services/worker/``；只改一边会变成三份实现，比现在更糟。
2. 收敛的前置条件"统一默认值语义"本身有坑。Worker 侧的
   ``DEFAULT_NAMESPACE: ClassVar[str] = redis_namespace()`` 与
   ``RedisKeyConfig.namespace: str = redis_namespace()`` 都在**类体/字段默认值**求值，
   是**导入期快照**而不是"跟随"：实测 ``import antcode_worker.transport.redis.keys``
   在没有 DATABASE_URL 的进程里直接抛 Settings 校验错，正是 ``settings_ref``
   那套惰性解析要避免的形状。照它收敛是把这个 bug 传播过去。
3. core 侧那个硬编码 ``"antcode"`` **当前不会**指向错误键空间：全部生产构造点都显式
   传 namespace（下面那条 AST 断言在盯），且 ``Settings.REDIS_NAMESPACE`` 有
   ``min_length=1`` 与 ``^[A-Za-z0-9][A-Za-z0-9:_-]*$``，所以
   ``RedisKeys(settings.REDIS_NAMESPACE)`` 与 ``RedisKeys(redis_namespace())`` 恒等。
   它是给未来调用方留的陷阱，不是现存的错键空间——"先修默认值再收敛"的前提不成立。

先例：``RetryService`` 两侧同名却是两个东西，上一轮只合并了队列后端。同名不是合并
理由，"两边都得改才能收敛"也不是本轮能付的代价。
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE_KEYS_MODULES = (
    "antcode_core.infrastructure.redis.keys",
    "antcode_core.infrastructure.redis",
)
SOURCE_ROOTS = (Path("packages"), Path("services"))
# 两侧同名同签名、且都活着的那八个；``spider_dedup`` 不在内，它只有 antcode_scrapy 一份。
SPIDER_KEY_METHODS = (
    "spider_data_stream",
    "spider_meta_key",
    "spider_item_ids_key",
    "spider_item_order_key",
    "spider_tombstone_key",
    "spider_index_key",
    "spider_index_expiry_key",
    "spider_config_key",
)


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


def test_core_redis_keys_does_not_shadow_the_task_plane_key_names() -> None:
    """core 版本只管 spider 数据面与运行日志，任务面 key 归 ``control_plane``。

    这四个名字曾在 core 侧有一份零生产调用方的副本，其中 ``heartbeat_key``
    与 ``consumer_group_name`` 生成的 key 与权威实现不同——用错副本会静默
    读到空数据。断言 worker 侧仍在，否则删错边时本条会假通过。
    """
    from antcode_core.infrastructure.redis.keys import RedisKeys as CoreRedisKeys
    from antcode_worker.transport.redis.keys import RedisKeys as WorkerRedisKeys

    task_plane = ("task_ready_stream", "task_result_stream", "heartbeat_key", "consumer_group_name")

    assert [name for name in task_plane if hasattr(WorkerRedisKeys, name)] == list(task_plane)
    assert [name for name in task_plane if hasattr(CoreRedisKeys, name)] == []


def test_both_spider_key_implementations_stay_byte_identical() -> None:
    """判定不收敛之后，唯一还能防住漂移的东西：逐字节交叉断言。

    写方（Worker）与读方（Gateway / web_api）用的是两个类。任何一侧单独改格式，
    生产上的表现是读到空数据、不报错，与"这次运行确实没抓到东西"逐字节相同。
    带非默认 namespace 的那一组同时钉住 hash tag 的位置——``{ns}`` 括在哪里错了，
    Redis Cluster 上就是跨 slot 的 Lua 直接失败。
    """
    from antcode_core.infrastructure.redis.keys import RedisKeys as CoreRedisKeys
    from antcode_worker.transport.redis.keys import RedisKeys as WorkerRedisKeys

    for namespace in ("antcode", "tenant-a"):
        core = CoreRedisKeys(namespace)
        worker = WorkerRedisKeys(namespace)
        produced = {name: (getattr(core, name)("id-1"), getattr(worker, name)("id-1")) for name in SPIDER_KEY_METHODS}
        drifted = {name: pair for name, pair in produced.items() if pair[0] != pair[1]}
        assert not drifted, f"两份 spider key 实现已漂移（namespace={namespace}）: {drifted}"
