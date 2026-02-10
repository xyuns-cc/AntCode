"""
运行时并发锁

实现并发锁机制，确保同一 runtime_hash 只有单一 builder。

Requirements: 6.5
"""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime

from loguru import logger


@dataclass
class LockInfo:
    """锁信息"""

    runtime_hash: str
    acquired_at: datetime
    holder_id: str
    timeout: float


@dataclass
class LockStats:
    """锁统计信息"""

    total_acquired: int = 0
    total_released: int = 0
    total_timeouts: int = 0
    total_contention: int = 0
    current_held: int = 0


class RuntimeLock:
    """
    运行时锁

    基于内存的异步锁，用于防止同一 runtime_hash 的并发构建。

    Requirements: 6.5
    """

    def __init__(
        self,
        default_timeout: float = 600.0,
        cleanup_interval: float = 60.0,
    ):
        """
        初始化锁管理器

        Args:
            default_timeout: 默认锁超时时间（秒）
            cleanup_interval: 清理过期锁的间隔（秒）
        """
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_info: dict[str, LockInfo] = {}
        self._default_timeout = default_timeout
        self._cleanup_interval = cleanup_interval
        self._stats = LockStats()
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """启动锁管理器"""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug("运行时锁管理器已启动")

    async def stop(self) -> None:
        """停止锁管理器"""
        self._running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        self._cleanup_task = None
        logger.debug("运行时锁管理器已停止")

    async def _cleanup_loop(self) -> None:
        """清理过期锁的循环"""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                if not self._running:
                    break

                await self._cleanup_expired_locks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理过期锁异常: {e}")

    async def _cleanup_expired_locks(self) -> None:
        """清理过期的锁"""
        now = datetime.now()
        expired = []

        for runtime_hash, info in self._lock_info.items():
            elapsed = (now - info.acquired_at).total_seconds()
            if elapsed > info.timeout:
                expired.append(runtime_hash)

        for runtime_hash in expired:
            logger.warning(f"锁超时，强制释放: {runtime_hash}")
            await self._force_release(runtime_hash)
            self._stats.total_timeouts += 1

    async def _force_release(self, runtime_hash: str) -> None:
        """强制释放锁"""
        if runtime_hash in self._lock_info:
            del self._lock_info[runtime_hash]
            self._stats.current_held -= 1

        # 注意：asyncio.Lock 不支持强制释放
        # 这里只清理元数据，实际的 Lock 对象会在下次获取时重建

    def _get_lock(self, runtime_hash: str) -> asyncio.Lock:
        """获取或创建锁对象"""
        if runtime_hash not in self._locks:
            self._locks[runtime_hash] = asyncio.Lock()
        return self._locks[runtime_hash]

    async def acquire(
        self,
        runtime_hash: str,
        holder_id: str = "",
        timeout: float | None = None,
        wait: bool = True,
    ) -> bool:
        """
        获取锁

        Args:
            runtime_hash: 运行时哈希
            holder_id: 持有者标识
            timeout: 超时时间（秒）
            wait: 是否等待锁

        Returns:
            是否成功获取锁
        """
        lock = self._get_lock(runtime_hash)
        timeout = timeout or self._default_timeout

        if wait:
            try:
                # 使用 wait_for 实现超时
                await asyncio.wait_for(lock.acquire(), timeout=timeout)
            except TimeoutError:
                logger.warning(f"获取锁超时: {runtime_hash}")
                self._stats.total_contention += 1
                return False
        else:
            if not lock.locked():
                await lock.acquire()
            else:
                self._stats.total_contention += 1
                return False

        # 记录锁信息
        self._lock_info[runtime_hash] = LockInfo(
            runtime_hash=runtime_hash,
            acquired_at=datetime.now(),
            holder_id=holder_id or f"holder-{id(asyncio.current_task())}",
            timeout=timeout,
        )
        self._stats.total_acquired += 1
        self._stats.current_held += 1

        logger.debug(f"获取锁成功: {runtime_hash}")
        return True

    async def release(self, runtime_hash: str) -> bool:
        """
        释放锁

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否成功释放
        """
        lock = self._locks.get(runtime_hash)
        if not lock:
            return False

        if not lock.locked():
            return False

        try:
            lock.release()
        except RuntimeError:
            # 锁未被当前任务持有
            return False

        # 清理锁信息
        if runtime_hash in self._lock_info:
            del self._lock_info[runtime_hash]

        self._stats.total_released += 1
        self._stats.current_held -= 1

        logger.debug(f"释放锁成功: {runtime_hash}")
        return True

    def is_locked(self, runtime_hash: str) -> bool:
        """检查是否已锁定"""
        lock = self._locks.get(runtime_hash)
        return lock is not None and lock.locked()

    def get_lock_info(self, runtime_hash: str) -> LockInfo | None:
        """获取锁信息"""
        return self._lock_info.get(runtime_hash)

    def get_stats(self) -> LockStats:
        """获取统计信息"""
        return self._stats

    @contextlib.asynccontextmanager
    async def lock(
        self,
        runtime_hash: str,
        holder_id: str = "",
        timeout: float | None = None,
    ) -> AsyncGenerator[bool, None]:
        """
        锁上下文管理器

        Args:
            runtime_hash: 运行时哈希
            holder_id: 持有者标识
            timeout: 超时时间

        Yields:
            是否成功获取锁

        Example:
            async with lock_manager.lock("abc123") as acquired:
                if acquired:
                    # 执行构建
                    pass
        """
        acquired = await self.acquire(runtime_hash, holder_id, timeout)
        try:
            yield acquired
        finally:
            if acquired:
                await self.release(runtime_hash)


class FileLock:
    """
    文件锁

    基于文件系统的锁，用于跨进程同步。
    适用于多 Worker 进程共享同一 runtimes 目录的场景。

    Requirements: 6.5
    """

    def __init__(
        self,
        locks_dir: str,
        default_timeout: float = 600.0,
        stale_timeout: float = 3600.0,
    ):
        """
        初始化文件锁管理器

        Args:
            locks_dir: 锁文件目录
            default_timeout: 默认锁超时时间（秒）
            stale_timeout: 过期锁清理时间（秒）
        """
        self.locks_dir = locks_dir
        self._default_timeout = default_timeout
        self._stale_timeout = stale_timeout

        # 确保目录存在
        os.makedirs(locks_dir, exist_ok=True)

    def _get_lock_file(self, runtime_hash: str) -> str:
        """获取锁文件路径"""
        return os.path.join(self.locks_dir, f"{runtime_hash}.lock")

    async def acquire(
        self,
        runtime_hash: str,
        holder_id: str = "",
        timeout: float | None = None,
    ) -> bool:
        """
        获取文件锁

        Args:
            runtime_hash: 运行时哈希
            holder_id: 持有者标识
            timeout: 超时时间

        Returns:
            是否成功获取锁
        """
        lock_file = self._get_lock_file(runtime_hash)
        timeout = timeout or self._default_timeout
        start_time = time.time()

        while True:
            # 检查是否超时
            if time.time() - start_time > timeout:
                return False

            # 尝试创建锁文件
            try:
                # 使用 O_CREAT | O_EXCL 确保原子性
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    # 写入锁信息
                    lock_info = {
                        "holder_id": holder_id or f"pid-{os.getpid()}",
                        "acquired_at": datetime.now().isoformat(),
                        "pid": os.getpid(),
                    }
                    os.write(fd, str(lock_info).encode())
                finally:
                    os.close(fd)

                logger.debug(f"获取文件锁成功: {runtime_hash}")
                return True

            except FileExistsError:
                # 锁文件已存在，检查是否过期
                if await self._is_stale(lock_file):
                    # 清理过期锁
                    try:
                        os.unlink(lock_file)
                        continue
                    except OSError:
                        pass

                # 等待后重试
                await asyncio.sleep(0.5)

            except OSError as e:
                logger.error(f"获取文件锁失败: {e}")
                return False

    async def _is_stale(self, lock_file: str) -> bool:
        """检查锁文件是否过期"""
        try:
            stat = os.stat(lock_file)
            age = time.time() - stat.st_mtime
            return age > self._stale_timeout
        except OSError:
            return True

    async def release(self, runtime_hash: str) -> bool:
        """
        释放文件锁

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否成功释放
        """
        lock_file = self._get_lock_file(runtime_hash)

        try:
            os.unlink(lock_file)
            logger.debug(f"释放文件锁成功: {runtime_hash}")
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            logger.error(f"释放文件锁失败: {e}")
            return False

    def is_locked(self, runtime_hash: str) -> bool:
        """检查是否已锁定"""
        lock_file = self._get_lock_file(runtime_hash)
        return os.path.exists(lock_file)

    @contextlib.asynccontextmanager
    async def lock(
        self,
        runtime_hash: str,
        holder_id: str = "",
        timeout: float | None = None,
    ) -> AsyncGenerator[bool, None]:
        """
        文件锁上下文管理器

        Args:
            runtime_hash: 运行时哈希
            holder_id: 持有者标识
            timeout: 超时时间

        Yields:
            是否成功获取锁
        """
        acquired = await self.acquire(runtime_hash, holder_id, timeout)
        try:
            yield acquired
        finally:
            if acquired:
                await self.release(runtime_hash)

    async def cleanup_stale_locks(self) -> int:
        """
        清理过期的锁文件

        Returns:
            清理的锁文件数量
        """
        cleaned = 0

        if not os.path.exists(self.locks_dir):
            return cleaned

        for filename in os.listdir(self.locks_dir):
            if not filename.endswith(".lock"):
                continue

            lock_file = os.path.join(self.locks_dir, filename)
            if await self._is_stale(lock_file):
                try:
                    os.unlink(lock_file)
                    cleaned += 1
                    logger.debug(f"清理过期锁文件: {filename}")
                except OSError:
                    pass

        return cleaned


# 全局锁管理器实例
_runtime_lock: RuntimeLock | None = None
_file_lock: FileLock | None = None


def get_runtime_lock() -> RuntimeLock:
    """获取全局运行时锁管理器"""
    global _runtime_lock
    if _runtime_lock is None:
        _runtime_lock = RuntimeLock()
    return _runtime_lock


def get_file_lock(locks_dir: str) -> FileLock:
    """获取文件锁管理器"""
    global _file_lock
    if _file_lock is None or _file_lock.locks_dir != locks_dir:
        _file_lock = FileLock(locks_dir)
    return _file_lock
