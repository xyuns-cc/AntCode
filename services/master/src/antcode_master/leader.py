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

        except RuntimeError as e:
            logger.debug(f"未能获取 Leader 锁: {e}")
            return False
        except Exception as e:
            logger.error(f"选主失败: {e}")
            return False

    async def step_down(self):
        """主动放弃 Leader 身份"""
        if not self._is_leader:
            return

        logger.info("主动放弃 Leader 身份")

        # 停止健康检查
        self._stop_health_check()

        # 释放锁
        if self._lock:
            await self._lock.release()
            self._lock = None

        self._is_leader = False
        self._fencing_token = None

    def _start_health_check(self):
        """启动健康检查任务"""
        if self._health_check_task is not None:
            return
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    def _stop_health_check(self):
        """停止健康检查任务"""
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            self._health_check_task = None

    async def _health_check_loop(self):
        """健康检查循环：主动去 Redis 权威校验，不信任 renew loop 的本地状态。"""
        while self._is_leader:
            try:
                await asyncio.sleep(self.ttl_seconds / 3)

                # B1: 用 verify_ownership 去 Redis 校验，而非依赖本地 is_locked。
                # renew 失败 → 本地 token=None → is_locked=False 已经会翻转；
                # 但更险的场景是：另一副本因时钟偏移/网络分区抢走了 lock 但
                # 我们还没被 CancelledError → 直接 GET 命中新值即可。
                if self._lock is None:
                    logger.warning("Leader 锁引用丢失，放弃 Leader 身份")
                    self._is_leader = False
                    self._fencing_token = None
                    break
                if not await self._lock.verify_ownership():
                    logger.warning("Leader 锁被抢占或已过期，放弃 Leader 身份")
                    self._is_leader = False
                    self._fencing_token = None
                    break

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查异常: {e}")

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

    新语义：内存 True 时再去 Redis 权威校验；不符即翻转内存状态，让 loop
    停止写。校验失败或 Redis 不可达时保守返回 False（宁可让出也不双写）。
    """
    if leader_election.is_leader:
        lock = leader_election._lock
        if lock is not None and await lock.verify_ownership():
            return True
        # 已被抢走 → 翻转本地状态
        logger.warning("ensure_leader 权威校验失败，放弃 Leader 身份")
        leader_election._is_leader = False
        leader_election._fencing_token = None
        return False

    return await leader_election.try_become_leader()


def get_fencing_token() -> int | None:
    """获取当前 Fencing Token

    Returns:
        Fencing Token，如果不是 Leader 则返回 None
    """
    return leader_election.fencing_token
