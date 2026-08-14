"""Redis 分布式锁、自动续租与 fencing token。"""

import asyncio
import time
import uuid

from loguru import logger
from redis.asyncio import Redis

from antcode_core.common.settings_ref import current_settings
from antcode_core.infrastructure.redis.client import get_redis_client


def _ns_prefix() -> str:
    """P1-a14: 从配置读 REDIS_NAMESPACE,生产选主锁与 fencing token 都必须带此前缀,
    否则多套部署共用同一 Redis DB 时会互相争主 / 抢 token,单调不变式被打破。
    """
    ns = (current_settings().REDIS_NAMESPACE or "antcode").strip().strip(":")
    return ns or "antcode"


class LeaderLockContendedError(RuntimeError):
    """Leader 锁正被其他实例持有。"""


class DistributedLock:
    """基于 SET NX EX、带所有权校验和可选自动续租的分布式锁。"""

    def __init__(
        self,
        key: str,
        ttl_seconds: int = 30,
        auto_renew: bool = False,
        renew_interval: float | None = None,
    ):
        """初始化锁；续租间隔默认是 TTL 的三分之一。"""
        # 每个部署使用独立命名空间；已规范化的 key 不重复加前缀。
        _ns = _ns_prefix()
        if key.startswith(f"{_ns}:"):
            self.key = key if key.startswith(f"{_ns}:lock:") else f"{_ns}:lock:{key[len(_ns) + 1 :]}"
        else:
            self.key = f"{_ns}:lock:{key}"
        self.ttl_seconds = ttl_seconds
        self.auto_renew = auto_renew
        self.renew_interval = renew_interval or (ttl_seconds / 3)

        self._token: str | None = None
        self._renew_task: asyncio.Task | None = None
        self._redis: Redis | None = None

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """获取锁；blocking=False 时只尝试一次。"""
        client = await self._get_client()
        self._token = str(uuid.uuid4())

        start_time = time.time()

        while True:
            # 尝试获取锁
            result = await client.set(
                self.key,
                self._token,
                nx=True,
                ex=self.ttl_seconds,
            )

            if result:
                logger.debug(f"获取锁成功: {self.key}")

                # 启动自动续期
                if self.auto_renew:
                    self._start_renew_task()

                return True

            if not blocking:
                self._token = None
                return False

            # 检查超时
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    self._token = None
                    return False

            # 等待一段时间后重试
            await asyncio.sleep(0.1)

    async def release(self) -> bool:
        """释放锁；返回是否成功释放。"""
        if not self._token:
            return False

        # 停止续期任务
        self._stop_renew_task()

        client = await self._get_client()

        # 使用 Lua 脚本确保只释放自己持有的锁
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """

        try:
            result = await client.eval(script, 1, self.key, self._token)
            self._token = None

            if result:
                logger.debug(f"释放锁成功: {self.key}")
                return True
            else:
                logger.warning(f"释放锁失败（锁已被其他持有者获取）: {self.key}")
                return False
        except Exception as e:
            logger.error(f"释放锁异常: {self.key}, 错误: {e}")
            self._token = None
            raise

    async def extend(self, additional_seconds: int | None = None) -> bool:
        """延长锁的过期时间；``additional_seconds`` 默认使用初始 TTL，返回是否成功延长。"""
        if not self._token:
            return False

        client = await self._get_client()
        ttl = additional_seconds or self.ttl_seconds

        # 使用 Lua 脚本确保只延长自己持有的锁
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """

        try:
            result = await client.eval(script, 1, self.key, self._token, ttl)
            return bool(result)
        except Exception as e:
            logger.error(f"延长锁失败: {self.key}, 错误: {e}")
            raise

    def _start_renew_task(self) -> None:
        """启动自动续期任务"""
        if self._renew_task is not None:
            return
        self._renew_task = asyncio.create_task(self._renew_loop())

    def _stop_renew_task(self) -> None:
        """停止自动续期任务"""
        task = self._renew_task
        self._renew_task = None
        if task is not None and not task.done():
            task.cancel()

    def abandon(self) -> None:
        """撤销本地锁所有权并停止续租，不对 Redis 做写操作。"""
        self._token = None
        self._stop_renew_task()

    async def _renew_loop(self) -> None:
        """续期循环。

        P2-07: 显式检查 ``extend()`` 返回值 —— 底层 Lua 走
        ``get==token ? expire : 0`` 语义,``bool(result)`` 明确区分
        ``0`` / ``1`` / ``None`` / 抛出。任何"没成功续到"的情况(锁被抢/
        已过期/Redis 抖动)都会把 ``_token`` 置空并 ``raise``,让上层
        ``LeaderElection.is_leader`` 立即通过 ``lock.is_locked`` 感知失租,
        不允许静默继续持有。
        """
        try:
            while True:
                await asyncio.sleep(self.renew_interval)

                if not self._token:
                    break

                success = await self.extend()
                if not success:
                    # extend 返回 False → Lua 里 GET 值 ≠ 我们的 token,
                    # 或 key 已消失 → 锁不再归我,主动放弃 + 抛给 LeaderElection。
                    self._token = None
                    raise RuntimeError(f"锁续期失败，锁可能已丢失: {self.key}")
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"锁续期异常: {self.key}, 错误: {e}")
            self._token = None
            raise
        finally:
            if self._renew_task is asyncio.current_task():
                self._renew_task = None

    async def __aenter__(self):
        """异步上下文管理器入口"""
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(f"无法获取锁: {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.release()

    @property
    def is_locked(self) -> bool:
        """本地视角：是否曾经获得过锁（未去过 Redis 校验）。

        注意：这个字段只表示"我有 token"，**不代表 Redis 里锁仍归我**。
        另一副本抢走或续约超时后 Redis 里 key 已改/已过期，本字段仍会
        返回 True 直到本地 renew loop 察觉。用 ``verify_ownership()``
        做真正的权威判断。
        """
        return self._token is not None

    async def verify_ownership(self) -> bool:
        """去 Redis 权威校验当前 leader 锁仍归本实例（token 匹配）。

        使用 Lua 脚本一次原子 GET+比较，避免 GET 后 key 被换、决策已错的
        窗口。校验不匹配或 Redis 不可达时立即撤销本地所有权并停止续租；
        Redis 异常继续向调用方传播，不能伪装成普通的锁竞争。
        """
        if not self._token:
            return False
        token = self._token
        try:
            client = await self._get_client()
            script = """
            local v = redis.call("get", KEYS[1])
            if v == ARGV[1] then
                return 1
            else
                return 0
            end
            """
            result = await client.eval(script, 1, self.key, token)
        except Exception as exc:
            self.abandon()
            logger.error(f"leader 锁权威校验异常，已停止续租: {self.key} - {exc}")
            raise

        owned = bool(result)
        if not owned:
            self.abandon()
        return owned


class FencingTokenManager:
    """Fencing Token 管理器

    用于防止旧 leader 的写入覆盖新 leader 的决策。
    每次获取 leader 锁时生成单调递增的 token。

    P1-a14: TOKEN_KEY 按 REDIS_NAMESPACE 拼前缀。否则两套不同 namespace 但共用
    Redis DB 的部署会共享同一 token 计数器,"新 leader token 必然大于旧" 的单调
    不变式被打破,过期写会被误接受。key 用属性而非 ``__init__`` 计算: 本模块的
    全局单例在 import 期构造,构造时读配置等于把控制面配置绑进导入链。
    """

    def __init__(self, token_name: str = "master"):
        self._redis: Redis | None = None
        self._current_token: int | None = None
        self._token_name = token_name

    @property
    def TOKEN_KEY(self) -> str:
        """``<namespace>:fencing:token:<name>``。"""
        return f"{_ns_prefix()}:fencing:token:{self._token_name}"

    @property
    def DISPATCH_TOKEN_KEY(self) -> str:
        """派发 mirror key,与 ready stream 共享 Cluster slot。"""
        return f"{{{_ns_prefix()}}}:fencing:dispatch:{self._token_name}"

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def acquire_token(self) -> int:
        """获取新的 fencing token

        Returns:
            单调递增的 token 值
        """
        client = await self._get_client()

        # Mirror 与旧计数器在 Redis Cluster 的不同 slot，不能放进同一 Lua。
        # 先失效 mirror，保证后续 INCR/SET 任一步失败时旧任期都不能继续 XADD。
        await client.delete(self.DISPATCH_TOKEN_KEY)
        token = await client.incr(self.TOKEN_KEY)
        await client.set(self.DISPATCH_TOKEN_KEY, token)
        self._current_token = token

        logger.info(f"获取 fencing token: {token}")
        return token

    async def get_current_token(self) -> int | None:
        """获取当前 token 值"""
        client = await self._get_client()

        result = await client.get(self.TOKEN_KEY)
        if result is None:
            return None

        return int(result)

    async def validate_token(self, token: int) -> bool:
        """验证 token 是否有效（是否为最新）

        Args:
            token: 要验证的 token

        Returns:
            token 是否有效
        """
        current = await self.get_current_token()

        if current is None:
            return False

        # token 必须等于当前值才有效
        return token == current

    async def validate_token_gte(self, token: int) -> bool:
        """验证 token 是否大于等于当前值

        用于检查写入是否来自有效的 leader。

        Args:
            token: 要验证的 token

        Returns:
            token 是否有效
        """
        current = await self.get_current_token()

        if current is None:
            return False

        return token >= current

    @property
    def local_token(self) -> int | None:
        """获取本地缓存的 token"""
        return self._current_token


# 全局 fencing token 管理器
fencing_token_manager = FencingTokenManager()


async def acquire_leader_lock(
    lock_key: str = "master",
    ttl_seconds: int = 30,
    auto_renew: bool = True,
) -> tuple[DistributedLock, int]:
    """获取 leader 锁并返回 fencing token

    Args:
        lock_key: 锁的 Key
        ttl_seconds: 锁的过期时间
        auto_renew: 是否自动续期

    Returns:
        (lock, fencing_token) 元组

    Raises:
        RuntimeError: 无法获取锁
    """
    lock = DistributedLock(
        key=f"leader:{lock_key}",
        ttl_seconds=ttl_seconds,
        auto_renew=auto_renew,
    )

    acquired = await lock.acquire(blocking=False)
    if not acquired:
        raise LeaderLockContendedError(f"无法获取 leader 锁: {lock_key}")

    try:
        token = await fencing_token_manager.acquire_token()
    except BaseException:
        # SET NX 已成功而 INCR 失败时必须撤销续租并补偿删除锁，否则会留下
        # 没有 fencing token、却持续续租的半完成 Leader 任期。
        await lock.release()
        raise

    return lock, token
