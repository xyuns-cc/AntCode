"""
Master 选主逻辑

实现基于 Redis 分布式锁的 Leader Election，支持：
- 自动选主
- Fencing Token 生成与校验
- Leader 健康检查
- 自动故障转移
"""

import asyncio

from loguru import logger

from antcode_core.infrastructure.redis.locks import (
    DistributedLock,
    acquire_leader_lock,
)


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
        """是否为 Leader"""
        return self._is_leader

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
            logger.info(
                f"成为 Leader: lock_key={self.lock_key}, "
                f"fencing_token={self._fencing_token}"
            )

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
        """健康检查循环"""
        while self._is_leader:
            try:
                await asyncio.sleep(self.ttl_seconds / 3)

                # 检查锁是否仍然持有
                if self._lock and not self._lock.is_locked:
                    logger.warning("检测到 Leader 锁丢失，放弃 Leader 身份")
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
    """确保当前实例是 Leader

    Returns:
        是否为 Leader
    """
    if leader_election.is_leader:
        return True

    return await leader_election.try_become_leader()


def get_fencing_token() -> int | None:
    """获取当前 Fencing Token

    Returns:
        Fencing Token，如果不是 Leader 则返回 None
    """
    return leader_election.fencing_token
