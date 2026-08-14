"""
Master 选主逻辑

实现基于 Redis 分布式锁的 Leader Election，支持：
- 自动选主
- Fencing Token 生成与校验
- Leader 健康检查
- 自动故障转移
"""

import asyncio

from antcode_core.infrastructure.redis.locks import (
    DistributedLock,
    LeaderLockContendedError,
    acquire_leader_lock,
)
from loguru import logger


class LeaderElection:
    """Leader 选举管理器"""

    def __init__(
        self,
        lock_key: str = "master",
        ttl_seconds: int = 30,
        auto_renew: bool = True,
    ):
        """初始化 Leader 选举

        Args:
            lock_key: 锁的 Key
            ttl_seconds: 锁的过期时间（秒）
            auto_renew: 是否自动续期
        """
        self.lock_key = lock_key
        self.ttl_seconds = ttl_seconds
        self.auto_renew = auto_renew

        self._lock: DistributedLock | None = None
        self._fencing_token: int | None = None
        self._is_leader = False
        self._health_check_task: asyncio.Task | None = None

    @property
    def is_leader(self) -> bool:
        """是否为 Leader（本地视角，未走 Redis 权威校验）。

        P2-07: 兜住 DistributedLock 续租失败但 LeaderElection 健康检查
        尚未察觉的窗口。renew_loop 检查 ``extend()`` 返回值发现 ``False``
        或抛异常时会先把 ``_lock._token`` 置空,本属性此时立刻感知并翻转
        ``_is_leader``,不再等 ``_health_check_loop`` 睡 ``ttl/3`` 才纠正。
        权威判断仍走 ``ensure_leader()`` → ``verify_ownership()``。
        """
        if not self._is_leader:
            return False
        # renew_task 已死(extend 抛/返回 False → _token=None)时,本地视角立刻放弃
        if self._lock is None or not self._lock.is_locked:
            if self._lock is not None:
                self._revoke_leadership(self._lock)
            else:
                self._is_leader = False
                self._fencing_token = None
            return False
        return True

    @property
    def fencing_token(self) -> int | None:
        """获取当前 Fencing Token"""
        return self._fencing_token

    async def try_become_leader(self) -> bool:
        """尝试成为 Leader

        Returns:
            是否成功成为 Leader
        """
        try:
            self._lock, self._fencing_token = await acquire_leader_lock(
                lock_key=self.lock_key,
                ttl_seconds=self.ttl_seconds,
                auto_renew=self.auto_renew,
            )

            self._is_leader = True
            logger.info(f"成为 Leader: lock_key={self.lock_key}, fencing_token={self._fencing_token}")

            # 启动健康检查
            if self.auto_renew:
                self._start_health_check()

            return True

        except LeaderLockContendedError as e:
            logger.debug(f"未能获取 Leader 锁: {e}")
            return False
        except Exception as e:
            logger.error(f"选主失败: {e}")
            raise

    async def step_down(self):
        """主动放弃 Leader 身份"""
        was_leader = self._is_leader
        lock = self._lock
        self._stop_health_check()
        self._lock = None
        self._is_leader = False
        self._fencing_token = None

        if was_leader:
            logger.info("主动放弃 Leader 身份")
        if lock:
            await lock.release()

    def _revoke_leadership(self, lock: DistributedLock) -> None:
        """撤销指定任期，避免旧健康检查误伤后来获得的新任期。"""
        lock.abandon()
        if self._lock is not lock:
            return
        self._stop_health_check()
        self._lock = None
        self._is_leader = False
        self._fencing_token = None

    async def verify_leadership(self) -> bool:
        """权威校验当前任期；失败时同步停止该任期的锁续租。"""
        lock = self._lock
        if not self.is_leader or lock is None:
            return False
        try:
            owned = await lock.verify_ownership()
        except Exception:
            self._revoke_leadership(lock)
            raise
        if not owned:
            self._revoke_leadership(lock)
        return owned and self._lock is lock and self._is_leader

    def _start_health_check(self):
        """启动健康检查任务"""
        if self._health_check_task is not None and not self._health_check_task.done():
            return
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    def _stop_health_check(self):
        """停止健康检查任务"""
        task = self._health_check_task
        self._health_check_task = None
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and task is not current and not task.done():
            task.cancel()

    async def _health_check_loop(self):
        """健康检查循环：主动去 Redis 权威校验，不信任 renew loop 的本地状态。"""
        task = asyncio.current_task()
        try:
            while self._is_leader:
                await asyncio.sleep(self.ttl_seconds / 3)

                if not await self.verify_leadership():
                    logger.warning("Leader 锁被抢占或已过期，放弃 Leader 身份")
                    break
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"健康检查异常，已撤销 Leader 任期: {e}")
            raise
        finally:
            if self._health_check_task is task:
                self._health_check_task = None

    async def validate_token(self, token: int) -> bool:
        """验证 Fencing Token 是否有效

        Args:
            token: 要验证的 token

        Returns:
            token 是否有效
        """
        from antcode_core.infrastructure.redis.locks import fencing_token_manager

        return await fencing_token_manager.validate_token(token)


# 全局 Leader 选举实例
leader_election = LeaderElection()


async def ensure_leader() -> bool:
    """确保当前实例是 Leader（权威模式）。

    B1: 旧实现只看内存 ``leader_election.is_leader`` 布尔就返回 True，
    另一副本抢走锁到 renew loop 察觉之间有一个 TTL 窗口（默认 10s）会
    出现"我以为我是 leader 但 Redis 里锁归别人"，导致两副本同时派发。

    新语义：内存 True 时再去 Redis 权威校验；不符即撤销本地任期并停止
    锁续租。Redis 不可达时同样撤销任期，同时向调用方传播基础设施异常。
    """
    if leader_election.is_leader:
        if await leader_election.verify_leadership():
            return True
        logger.warning("ensure_leader 权威校验失败，放弃 Leader 身份")
        return False

    return await leader_election.try_become_leader()


def get_fencing_token() -> int | None:
    """获取当前 Fencing Token

    Returns:
        Fencing Token，如果不是 Leader 则返回 None
    """
    return leader_election.fencing_token


async def require_fencing_token() -> int:
    """Return the current verified Leader epoch or fail closed."""
    if not await ensure_leader():
        raise RuntimeError("当前实例不是 Redis 权威 Leader")
    token = leader_election.fencing_token
    if token is None:
        raise RuntimeError("Leader 缺少 fencing token")
    return token
