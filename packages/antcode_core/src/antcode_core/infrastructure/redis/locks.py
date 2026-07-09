"""Redis 分布式锁

提供分布式锁实现，支持：
- 基本锁（acquire/release）
- 可重入锁
- Fencing Token（防止旧 leader 写入）
- 自动续期
"""

import asyncio
import time
import uuid

from loguru import logger

from antcode_core.infrastructure.redis.client import get_redis_client


class DistributedLock:
    """Redis 分布式锁

    使用 SET NX EX 实现的分布式锁，支持：
    - 自动过期
    - 安全释放（只释放自己持有的锁）
    - 可选的自动续期
    """

    def __init__(
        self,
        key: str,
        ttl_seconds: int = 30,
        auto_renew: bool = False,
        renew_interval: float | None = None,
    ):
        """初始化分布式锁

        Args:
            key: 锁的 Key
            ttl_seconds: 锁的过期时间（秒）
            auto_renew: 是否自动续期
            renew_interval: 续期间隔（秒），默认为 ttl 的 1/3
        """
        self.key = f"lock:{key}"
        self.ttl_seconds = ttl_seconds
        self.auto_renew = auto_renew
        self.renew_interval = renew_interval or (ttl_seconds / 3)

        self._token: str | None = None
        self._renew_task: asyncio.Task | None = None
        self._redis = None

    async def _get_client(self):
        """获取 Redis 客户端"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        """获取锁

        Args:
            blocking: 是否阻塞等待
            timeout: 阻塞超时时间（秒）

        Returns:
            是否成功获取锁
        """
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
        """释放锁

        Returns:
            是否成功释放
        """
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
        """延长锁的过期时间

        Args:
            additional_seconds: 额外的秒数，默认使用初始 TTL

        Returns:
            是否成功延长
        """
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
        if self._renew_task is not None:
            self._renew_task.cancel()
            self._renew_task = None

    async def _renew_loop(self) -> None:
        """续期循环"""
        while True:
            try:
                await asyncio.sleep(self.renew_interval)

                if not self._token:
                    break

                success = await self.extend()
                if not success:
                    self._token = None
                    raise RuntimeError(f"锁续期失败，锁可能已丢失: {self.key}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"锁续期异常: {self.key}, 错误: {e}")
                self._token = None
                raise

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
        窗口。Redis 不可达时保守返回 False（宁可让出 leader 也不双写）。
        """
        if not self._token:
            return False
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
            result = await client.eval(script, 1, self.key, self._token)
            return bool(result)
        except Exception as exc:
            logger.warning(f"leader 锁权威校验失败(保守返回 False): {self.key} - {exc}")
            return False


class FencingTokenManager:
    """Fencing Token 管理器

    用于防止旧 leader 的写入覆盖新 leader 的决策。
    每次获取 leader 锁时生成单调递增的 token。
    """

    TOKEN_KEY = "fencing:token:master"

    def __init__(self):
        self._redis = None
        self._current_token: int | None = None

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

        # 使用 INCR 确保单调递增
        token = await client.incr(self.TOKEN_KEY)
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
        raise RuntimeError(f"无法获取 leader 锁: {lock_key}")

    # 获取新的 fencing token
    token = await fencing_token_manager.acquire_token()

    return lock, token
