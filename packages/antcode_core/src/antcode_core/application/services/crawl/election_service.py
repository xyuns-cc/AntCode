"""Master 选举服务

实现基于 Redis 分布式锁的 Master 高可用选举机制。

需求: 5.1, 5.2, 5.3, 5.4, 5.5
"""

import asyncio
import contextlib

from loguru import logger

from antcode_core.infrastructure.redis.client import get_redis_client

# Redis 键名
MASTER_LOCK_KEY = "master:lock"

# 锁配置
DEFAULT_LOCK_TTL = 30  # 锁过期时间（秒）
DEFAULT_HEARTBEAT_INTERVAL = 10  # 心跳间隔（秒）
DEFAULT_WATCH_INTERVAL = 5  # Standby 监听间隔（秒）


class MasterElectionService:
    """Master 选举服务

    实现分布式 Master 选举和高可用切换：
    - Leader 获取和续期
    - Standby 监听和接管
    - 状态管理

    需求: 5.1, 5.2, 5.3, 5.4, 5.5
    """

    def __init__(
        self,
        worker_id: str,
        lock_ttl: int = DEFAULT_LOCK_TTL,
        heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL,
        watch_interval: int = DEFAULT_WATCH_INTERVAL,
        redis_client: object | None = None,
        enable_background_tasks: bool = True,
    ):
        """初始化选举服务

        Args:
            worker_id: 节点唯一标识
            lock_ttl: 锁过期时间（秒）
            heartbeat_interval: 心跳间隔（秒）
            watch_interval: Standby 监听间隔（秒）
        """
        self._worker_id = worker_id
        self._lock_ttl = lock_ttl
        self._heartbeat_interval = heartbeat_interval
        self._watch_interval = watch_interval
        self._redis_client = redis_client
        self._enable_background_tasks = enable_background_tasks

        self._is_leader = False
        self._heartbeat_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._shutdown = False

        logger.info(f"初始化 Master 选举服务: worker_id={worker_id}, "
                    f"lock_ttl={lock_ttl}s, heartbeat={heartbeat_interval}s")

    async def _get_redis(self):
        """获取 Redis 客户端（支持注入，便于测试）"""
        if self._redis_client is not None:
            return self._redis_client
        return await get_redis_client()

    # =========================================================================
    # Leader 选举
    # =========================================================================

    async def try_become_leader(self) -> bool:
        """尝试成为 Leader

        使用 Redis SET NX EX 命令获取分布式锁。

        Returns:
            是否成功成为 Leader

        需求: 5.1 - Master 启动时尝试获取 Redis 分布式锁成为 Leader
        """
        try:
            redis = await self._get_redis()

            # 使用 SET key value NX EX ttl
            # NX: 仅当键不存在时设置
            # EX: 设置过期时间（秒）
            result = await redis.set(
                MASTER_LOCK_KEY,
                self._worker_id,
                nx=True,
                ex=self._lock_ttl,
            )

            if result:
                self._is_leader = True
                logger.info(f"成为 Leader: worker_id={self._worker_id}")

                # 启动心跳任务
                if self._enable_background_tasks:
                    await self._start_heartbeat()

                return True
            else:
                # 锁已被其他节点持有
                current_leader = await redis.get(MASTER_LOCK_KEY)
                if current_leader:
                    current_leader = current_leader.decode('utf-8')

                logger.debug(f"未能成为 Leader: worker_id={self._worker_id}, "
                            f"current_leader={current_leader}")

                return False

        except Exception as e:
            logger.error(f"尝试成为 Leader 失败: worker_id={self._worker_id}, 错误: {e}")
            return False

    async def is_leader(self) -> bool:
        """检查当前节点是否为 Leader

        通过检查 Redis 锁的持有者来确认。

        Returns:
            是否为 Leader
        """
        if not self._is_leader:
            return False

        try:
            redis = await self._get_redis()
            current_holder = await redis.get(MASTER_LOCK_KEY)

            if current_holder:
                current_holder = current_holder.decode('utf-8')
                return current_holder == self._worker_id
            else:
                # 锁已过期
                self._is_leader = False
                return False

        except Exception as e:
            logger.error(f"检查 Leader 状态失败: {e}")
            return False

    async def resign_leadership(self):
        """主动放弃 Leader 角色

        删除 Redis 锁并停止心跳。
        """
        if not self._is_leader:
            return

        try:
            # 停止心跳
            await self._stop_heartbeat()

            # 删除锁（仅当持有者是自己时）
            redis = await self._get_redis()

            # 使用 Lua 脚本确保原子性
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """

            await redis.eval(lua_script, 1, MASTER_LOCK_KEY, self._worker_id)

            self._is_leader = False
            logger.info(f"放弃 Leader 角色: worker_id={self._worker_id}")

        except Exception as e:
            logger.error(f"放弃 Leader 角色失败: {e}")

    # =========================================================================
    # Leader 心跳
    # =========================================================================

    async def heartbeat(self) -> bool:
        """Leader 心跳续期

        定期续期锁以保持 Leader 角色。

        Returns:
            是否成功续期

        需求: 5.2 - Master 持有锁时定期续期锁并执行调度任务
        """
        if not self._is_leader:
            return False

        try:
            redis = await self._get_redis()

            # 使用 Lua 脚本确保原子性：仅当持有者是自己时才续期
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("expire", KEYS[1], ARGV[2])
            else
                return 0
            end
            """

            result = await redis.eval(
                lua_script,
                1,
                MASTER_LOCK_KEY,
                self._worker_id,
                self._lock_ttl,
            )

            if result:
                logger.debug(f"Leader 心跳成功: worker_id={self._worker_id}")
                return True
            else:
                # 锁已被其他节点持有，失去 Leader 角色
                logger.warning(f"失去 Leader 角色: worker_id={self._worker_id}")
                self._is_leader = False
                await self._stop_heartbeat()
                return False

        except Exception as e:
            logger.error(f"Leader 心跳失败: {e}")
            return False

    async def _start_heartbeat(self):
        """启动心跳任务"""
        if not self._enable_background_tasks:
            return
        if self._heartbeat_task and not self._heartbeat_task.done():
            return

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"启动 Leader 心跳: worker_id={self._worker_id}, "
                    f"interval={self._heartbeat_interval}s")

    async def _stop_heartbeat(self):
        """停止心跳任务"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task

            self._heartbeat_task = None
            logger.info(f"停止 Leader 心跳: worker_id={self._worker_id}")

    async def _heartbeat_loop(self):
        """心跳循环

        需求: 5.2 - Master 持有锁时定期续期锁
        需求: 5.3 - Master 失去锁时降级为 Standby 模式
        """
        while not self._shutdown and self._is_leader:
            try:
                await asyncio.sleep(self._heartbeat_interval)

                success = await self.heartbeat()

                if not success:
                    # 失去 Leader 角色，降级为 Standby
                    logger.warning(f"降级为 Standby: worker_id={self._worker_id}")
                    self._is_leader = False

                    # 启动 Standby 监听
                    await self._start_watch()
                    break

            except asyncio.CancelledError:
                logger.debug("心跳任务已取消")
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(1)  # 短暂延迟后重试

    # =========================================================================
    # Standby 监听
    # =========================================================================

    async def watch_leader(self) -> bool:
        """Standby 监听 Leader

        检查锁是否过期，如果过期则尝试抢锁。

        Returns:
            是否成功接管 Leader 角色

        需求: 5.4 - Standby 检测到锁过期时尝试抢锁并接管 Leader 角色
        """
        try:
            redis = await self._get_redis()

            # 检查锁是否存在
            current_holder = await redis.get(MASTER_LOCK_KEY)

            if not current_holder:
                # 锁已过期，尝试抢锁
                logger.info(f"检测到 Leader 锁过期，尝试接管: worker_id={self._worker_id}")

                success = await self.try_become_leader()

                if success:
                    logger.info(f"成功接管 Leader 角色: worker_id={self._worker_id}")

                    # 停止 Standby 监听
                    await self._stop_watch()

                    # 执行接管后的恢复任务
                    await self._on_leader_takeover()

                    return True
                else:
                    logger.debug(f"接管失败，其他节点已抢先: worker_id={self._worker_id}")
                    return False
            else:
                # 锁仍然有效
                current_holder = current_holder.decode('utf-8')
                logger.debug(f"Leader 仍然活跃: current_leader={current_holder}")
                return False

        except Exception as e:
            logger.error(f"监听 Leader 失败: {e}")
            return False

    async def start_as_standby(self):
        """以 Standby 模式启动

        启动监听任务，等待接管 Leader 角色。
        """
        if self._is_leader:
            logger.warning("当前节点已是 Leader，无需启动 Standby 模式")
            return
        if not self._enable_background_tasks:
            logger.info(f"Standby 模式已禁用后台任务: worker_id={self._worker_id}")
            return

        await self._start_watch()
        logger.info(f"以 Standby 模式启动: worker_id={self._worker_id}")

    async def _start_watch(self):
        """启动 Standby 监听任务"""
        if not self._enable_background_tasks:
            return
        if self._watch_task and not self._watch_task.done():
            return

        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info(f"启动 Standby 监听: worker_id={self._worker_id}, "
                    f"interval={self._watch_interval}s")

    async def _stop_watch(self):
        """停止 Standby 监听任务"""
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task

            self._watch_task = None
            logger.info(f"停止 Standby 监听: worker_id={self._worker_id}")

    async def _watch_loop(self):
        """Standby 监听循环

        需求: 5.4 - Standby 检测到锁过期时尝试抢锁并接管 Leader 角色
        """
        while not self._shutdown and not self._is_leader:
            try:
                await asyncio.sleep(self._watch_interval)

                # 检查并尝试接管
                success = await self.watch_leader()

                if success:
                    # 成功接管，退出监听循环
                    break

            except asyncio.CancelledError:
                logger.debug("监听任务已取消")
                break
            except Exception as e:
                logger.error(f"监听循环异常: {e}")
                await asyncio.sleep(1)  # 短暂延迟后重试

    async def _on_leader_takeover(self):
        """Leader 接管后的恢复任务

        需求: 5.5 - 新 Leader 接管时扫描 PEL 中超时任务并恢复调度
        """
        logger.info(f"执行 Leader 接管恢复任务: worker_id={self._worker_id}")

        # TODO: 实现以下恢复任务
        # 1. 扫描所有项目的 PEL（Pending Entries List）
        # 2. 识别超时任务
        # 3. 使用 XCLAIM 转移超时任务
        # 4. 恢复批次调度

        # 这部分逻辑需要与 CrawlQueueService 集成
        # 暂时记录日志，后续实现
        logger.info("Leader 接管恢复任务完成")

    # =========================================================================
    # 状态管理
    # =========================================================================

    def get_status(self) -> dict:
        """获取当前状态

        Returns:
            状态信息字典
        """
        return {
            "worker_id": self._worker_id,
            "is_leader": self._is_leader,
            "lock_ttl": self._lock_ttl,
            "heartbeat_interval": self._heartbeat_interval,
            "watch_interval": self._watch_interval,
            "heartbeat_running": self._heartbeat_task is not None and not self._heartbeat_task.done(),
            "watch_running": self._watch_task is not None and not self._watch_task.done(),
        }

    async def get_current_leader(self) -> str | None:
        """获取当前 Leader 节点 ID

        Returns:
            Leader 节点 ID，无 Leader 时返回 None
        """
        try:
            redis = await self._get_redis()
            current_holder = await redis.get(MASTER_LOCK_KEY)

            if current_holder:
                return current_holder.decode('utf-8')
            else:
                return None

        except Exception as e:
            logger.error(f"获取当前 Leader 失败: {e}")
            return None

    async def get_lock_ttl(self) -> int | None:
        """获取锁的剩余 TTL

        Returns:
            剩余 TTL（秒），锁不存在时返回 None
        """
        try:
            redis = await self._get_redis()
            ttl = await redis.ttl(MASTER_LOCK_KEY)

            if ttl > 0:
                return ttl
            else:
                return None

        except Exception as e:
            logger.error(f"获取锁 TTL 失败: {e}")
            return None

    # =========================================================================
    # 生命周期管理
    # =========================================================================

    async def start(self):
        """启动选举服务

        尝试成为 Leader，失败则以 Standby 模式运行。
        """
        self._shutdown = False

        # 尝试成为 Leader
        success = await self.try_become_leader()

        if not success:
            # 以 Standby 模式启动
            await self.start_as_standby()

    async def shutdown(self):
        """关闭选举服务

        停止所有后台任务并释放资源。
        """
        self._shutdown = True

        # 停止心跳
        await self._stop_heartbeat()

        # 停止监听
        await self._stop_watch()

        # 如果是 Leader，放弃角色
        if self._is_leader:
            await self.resign_leadership()

        logger.info(f"选举服务已关闭: worker_id={self._worker_id}")


# 工厂函数
def create_election_service(
    worker_id: str,
    lock_ttl: int = DEFAULT_LOCK_TTL,
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL,
    watch_interval: int = DEFAULT_WATCH_INTERVAL,
    redis_client: object | None = None,
    enable_background_tasks: bool = True,
) -> MasterElectionService:
    """创建选举服务实例

    Args:
        worker_id: 节点唯一标识
        lock_ttl: 锁过期时间（秒）
        heartbeat_interval: 心跳间隔（秒）
        watch_interval: Standby 监听间隔（秒）

    Returns:
        MasterElectionService 实例
    """
    return MasterElectionService(
        worker_id=worker_id,
        lock_ttl=lock_ttl,
        heartbeat_interval=heartbeat_interval,
        watch_interval=watch_interval,
        redis_client=redis_client,
        enable_background_tasks=enable_background_tasks,
    )
