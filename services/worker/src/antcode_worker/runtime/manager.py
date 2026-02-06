"""
运行时管理器

整合 spec/hash/builder/locks/gc，提供统一的运行时管理接口。
返回 RuntimeHandle(path, runtime_hash)。

Requirements: 6.1
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from antcode_worker.domain.models import RuntimeHandle
from antcode_worker.runtime.builder import RuntimeBuilder
from antcode_worker.runtime.gc import GCPolicy, RuntimeGC
from antcode_worker.runtime.hash import compute_runtime_hash
from antcode_worker.runtime.locks import RuntimeLock
from antcode_worker.runtime.spec import RuntimeSpec


@dataclass
class RuntimeManagerConfig:
    """运行时管理器配置"""

    # 虚拟环境目录
    venvs_dir: str

    # 锁目录（用于文件锁）
    locks_dir: str | None = None

    # 构建超时（秒）
    build_timeout: int = 600

    # 锁超时（秒）
    lock_timeout: float = 600.0

    # uv 缓存目录
    uv_cache_dir: str | None = None

    # GC 策略
    gc_policy: GCPolicy | None = None

    # 是否启用自动 GC
    auto_gc: bool = True


class RuntimeManager:
    """
    运行时管理器

    整合 spec/hash/builder/locks/gc，提供统一的运行时管理接口。

    Requirements: 6.1

    主要功能：
    - prepare(): 准备运行时环境，返回 RuntimeHandle
    - release(): 释放运行时（更新最后使用时间）
    - remove(): 删除运行时
    - list(): 列出所有运行时
    """

    def __init__(self, config: RuntimeManagerConfig):
        """
        初始化运行时管理器

        Args:
            config: 管理器配置
        """
        self.config = config

        # 确保目录存在
        os.makedirs(config.venvs_dir, exist_ok=True)
        if config.locks_dir:
            os.makedirs(config.locks_dir, exist_ok=True)

        # 初始化组件
        self._builder = RuntimeBuilder(
            venvs_dir=config.venvs_dir,
            timeout=config.build_timeout,
            uv_cache_dir=config.uv_cache_dir,
        )

        self._lock = RuntimeLock(default_timeout=config.lock_timeout)

        self._gc = RuntimeGC(
            venvs_dir=config.venvs_dir,
            policy=config.gc_policy or GCPolicy(auto_gc=config.auto_gc),
        )

        # 运行时使用计数
        self._usage_count: dict[str, int] = {}

        # 运行状态
        self._running = False

    async def start(self) -> None:
        """启动运行时管理器"""
        if self._running:
            return

        self._running = True

        # 启动锁管理器
        await self._lock.start()

        # 启动 GC
        await self._gc.start()

        logger.info("运行时管理器已启动")

    async def stop(self) -> None:
        """停止运行时管理器"""
        self._running = False

        # 停止 GC
        await self._gc.stop()

        # 停止锁管理器
        await self._lock.stop()

        logger.info("运行时管理器已停止")

    async def prepare(
        self,
        spec: RuntimeSpec,
        force_rebuild: bool = False,
        wait_for_lock: bool = True,
    ) -> RuntimeHandle:
        """
        准备运行时环境

        如果运行时已存在且有效，直接返回；否则构建新的运行时。
        使用锁确保同一 runtime_hash 只有一个构建进程。

        Args:
            spec: 运行时规格
            force_rebuild: 是否强制重建
            wait_for_lock: 是否等待锁

        Returns:
            RuntimeHandle 包含路径和哈希

        Raises:
            RuntimeError: 构建失败或获取锁失败

        Requirements: 6.1
        """
        # 计算哈希
        runtime_hash = compute_runtime_hash(spec)

        logger.debug(f"准备运行时: {runtime_hash}")

        # 获取锁
        async with self._lock.lock(
            runtime_hash,
            timeout=self.config.lock_timeout if wait_for_lock else 0,
        ) as acquired:
            if not acquired:
                raise RuntimeError(f"无法获取运行时锁: {runtime_hash}")

            # 构建运行时
            result = await self._builder.build(spec, force_rebuild=force_rebuild)

            if not result.success:
                raise RuntimeError(f"构建运行时失败: {result.error_message}")

            # 更新使用计数
            self._usage_count[runtime_hash] = self._usage_count.get(runtime_hash, 0) + 1

            # 创建 RuntimeHandle
            handle = RuntimeHandle(
                path=result.venv_path,
                runtime_hash=runtime_hash,
                python_executable=result.python_executable,
                python_version=result.python_version,
                created_at=datetime.now() if not result.cached else None,
                last_used_at=datetime.now(),
            )

            logger.info(
                f"运行时准备完成: {runtime_hash}, "
                f"cached={result.cached}, "
                f"build_time={result.build_time_ms:.0f}ms"
            )

            return handle

    async def release(self, handle: RuntimeHandle) -> None:
        """
        释放运行时

        更新最后使用时间，减少使用计数。

        Args:
            handle: 运行时句柄
        """
        runtime_hash = handle.runtime_hash

        # 减少使用计数
        if runtime_hash in self._usage_count:
            self._usage_count[runtime_hash] = max(0, self._usage_count[runtime_hash] - 1)

        # 更新最后使用时间
        self._builder._update_last_used(handle.path)

        logger.debug(f"释放运行时: {runtime_hash}")

    async def remove(self, runtime_hash: str, force: bool = False) -> bool:
        """
        删除运行时

        Args:
            runtime_hash: 运行时哈希
            force: 是否强制删除（即使正在使用）

        Returns:
            是否成功删除
        """
        # 检查是否正在使用
        if not force and self._usage_count.get(runtime_hash, 0) > 0:
            logger.warning(f"运行时正在使用，无法删除: {runtime_hash}")
            return False

        # 获取锁
        async with self._lock.lock(runtime_hash, timeout=10) as acquired:
            if not acquired:
                logger.warning(f"无法获取锁，删除失败: {runtime_hash}")
                return False

            # 删除运行时
            success = await self._builder.remove(runtime_hash)

            if success:
                # 清理使用计数
                self._usage_count.pop(runtime_hash, None)

            return success

    async def list_runtimes(self) -> list[dict[str, Any]]:
        """
        列出所有运行时

        Returns:
            运行时信息列表
        """
        runtimes = await self._builder.list_runtimes()

        # 添加使用计数
        for rt in runtimes:
            rt["usage_count"] = self._usage_count.get(rt["runtime_hash"], 0)

        return runtimes

    async def get_runtime(self, runtime_hash: str) -> dict[str, Any] | None:
        """
        获取运行时信息

        Args:
            runtime_hash: 运行时哈希

        Returns:
            运行时信息或 None
        """
        runtimes = await self._builder.list_runtimes()

        for rt in runtimes:
            if rt["runtime_hash"] == runtime_hash:
                rt["usage_count"] = self._usage_count.get(runtime_hash, 0)
                return rt

        return None

    def exists(self, runtime_hash: str) -> bool:
        """
        检查运行时是否存在

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否存在
        """
        return self._builder.exists(runtime_hash)

    def is_in_use(self, runtime_hash: str) -> bool:
        """
        检查运行时是否正在使用

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否正在使用
        """
        return self._usage_count.get(runtime_hash, 0) > 0

    async def run_gc(self) -> dict[str, Any]:
        """
        手动触发垃圾回收

        Returns:
            GC 结果
        """
        return await self._gc.run_gc()

    def get_stats(self) -> dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        gc_stats = self._gc.stats
        lock_stats = self._lock.get_stats()

        return {
            "runtime_count": self._gc.get_runtime_count(),
            "total_size_bytes": self._gc.get_total_size(),
            "active_count": sum(1 for c in self._usage_count.values() if c > 0),
            "gc": {
                "last_gc_time": gc_stats.last_gc_time.isoformat() if gc_stats.last_gc_time else None,
                "total_gc_runs": gc_stats.total_gc_runs,
                "total_cleaned": gc_stats.total_cleaned,
                "total_bytes_freed": gc_stats.total_bytes_freed,
            },
            "locks": {
                "total_acquired": lock_stats.total_acquired,
                "total_released": lock_stats.total_released,
                "total_timeouts": lock_stats.total_timeouts,
                "total_contention": lock_stats.total_contention,
                "current_held": lock_stats.current_held,
            },
        }


# 全局运行时管理器实例
_runtime_manager: RuntimeManager | None = None


def get_runtime_manager() -> RuntimeManager | None:
    """获取全局运行时管理器"""
    return _runtime_manager


def set_runtime_manager(manager: RuntimeManager) -> None:
    """设置全局运行时管理器"""
    global _runtime_manager
    _runtime_manager = manager


async def create_runtime_manager(
    venvs_dir: str,
    locks_dir: str | None = None,
    build_timeout: int = 600,
    lock_timeout: float = 600.0,
    uv_cache_dir: str | None = None,
    gc_policy: GCPolicy | None = None,
    auto_gc: bool = True,
) -> RuntimeManager:
    """
    创建并启动运行时管理器

    Args:
        venvs_dir: 虚拟环境目录
        locks_dir: 锁目录
        build_timeout: 构建超时
        lock_timeout: 锁超时
        uv_cache_dir: uv 缓存目录
        gc_policy: GC 策略
        auto_gc: 是否启用自动 GC

    Returns:
        运行时管理器实例
    """
    config = RuntimeManagerConfig(
        venvs_dir=venvs_dir,
        locks_dir=locks_dir,
        build_timeout=build_timeout,
        lock_timeout=lock_timeout,
        uv_cache_dir=uv_cache_dir,
        gc_policy=gc_policy,
        auto_gc=auto_gc,
    )

    manager = RuntimeManager(config)
    await manager.start()

    set_runtime_manager(manager)

    return manager
