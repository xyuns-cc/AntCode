"""
运行时垃圾回收

实现 TTL/LRU/disk watermark 清理策略。

Requirements: 6.6
"""

import asyncio
import contextlib
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import ujson
from loguru import logger

from antcode_worker.runtime.gc_types import GCRunResult


@dataclass
class GCPolicy:
    """
    垃圾回收策略

    支持多种清理策略的组合：
    - TTL: 基于时间的过期清理
    - LRU: 基于最近使用时间的清理
    - Disk Watermark: 基于磁盘使用率的清理
    """

    # TTL 策略：运行时过期时间（秒），0 表示不启用
    ttl_seconds: int = 7 * 24 * 3600  # 默认 7 天

    # LRU 策略：最大保留数量，0 表示不限制
    max_count: int = 100

    # Disk Watermark 策略
    disk_high_watermark: float = 0.85  # 高水位（开始清理）
    disk_low_watermark: float = 0.70  # 低水位（停止清理）

    # 清理间隔（秒）
    gc_interval: int = 3600  # 默认 1 小时

    # 是否启用自动 GC
    auto_gc: bool = True

    # 最小保留数量（即使超过水位也保留）
    min_keep: int = 5


@dataclass
class GCStats:
    """垃圾回收统计"""

    last_gc_time: datetime | None = None
    total_gc_runs: int = 0
    total_cleaned: int = 0
    total_bytes_freed: int = 0
    last_cleaned: int = 0
    last_bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RuntimeInfo:
    """运行时信息"""

    runtime_hash: str
    path: str
    size_bytes: int
    created_at: datetime | None
    last_used_at: datetime | None


class RuntimeGC:
    """
    运行时垃圾回收器

    实现 TTL/LRU/disk watermark 清理策略。

    Requirements: 6.6
    """

    def __init__(
        self,
        venvs_dir: str,
        policy: GCPolicy | None = None,
        *,
        in_use_check: Callable[[str], bool] | None = None,
        cleanup_handler: Callable[[str], Awaitable[bool]] | None = None,
    ):
        """
        初始化垃圾回收器

        Args:
            venvs_dir: 虚拟环境目录
            policy: 清理策略
            in_use_check: 判断某 runtime_hash 是否正在被任务持有；返回 True 时 GC 跳过。
                          由 RuntimeManager 注入以避免在任务运行中被 rmtree 抢走目录。
            cleanup_handler: 在外部并发锁内执行删除的异步回调。
        """
        self.venvs_dir = venvs_dir
        self.policy = policy or GCPolicy()
        self._stats = GCStats()
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_gc_complete: Callable[[GCStats], None] | None = None
        self._in_use_check = in_use_check
        self._cleanup_handler = cleanup_handler

    def set_in_use_check(self, check: Callable[[str], bool] | None) -> None:
        """允许延迟绑定 in_use_check（RuntimeManager 构造顺序）。"""
        self._in_use_check = check

    @property
    def stats(self) -> GCStats:
        """获取统计信息"""
        return self._stats

    def set_gc_callback(self, callback: Callable[[GCStats], None]) -> None:
        """设置 GC 完成回调"""
        self._on_gc_complete = callback

    async def start(self) -> None:
        """启动自动 GC"""
        if self._running:
            return

        if not self.policy.auto_gc:
            logger.info("运行时自动 GC 已禁用")
            return

        self._running = True
        self._task = asyncio.create_task(self._gc_loop())
        logger.info(f"运行时 GC 已启动，间隔: {self.policy.gc_interval}s")

    async def stop(self) -> None:
        """停止自动 GC"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        logger.info("运行时 GC 已停止")

    async def _gc_loop(self) -> None:
        """GC 循环"""
        while self._running:
            try:
                await asyncio.sleep(self.policy.gc_interval)
                if not self._running:
                    break

                result = await self.run_gc()
                logger.info(
                    f"运行时 GC 完成: cleaned={result['cleaned']}, freed={result['bytes_freed'] / 1024 / 1024:.2f}MB"
                )

                if self._on_gc_complete:
                    try:
                        self._on_gc_complete(self._stats)
                    except Exception as e:
                        logger.error(f"GC 回调异常: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GC 循环异常: {e}")
                await asyncio.sleep(60)

    def _get_dir_size_sync(self, path: str) -> int:
        """同步版：目录大小（供 to_thread 包裹）。"""
        total = 0
        try:
            for dirpath, _dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    with contextlib.suppress(OSError, FileNotFoundError):
                        total += os.path.getsize(fp)
        except Exception:
            pass
        return total

    async def _get_dir_size(self, path: str) -> int:
        """G1: 目录大小走 to_thread，避免 os.walk 卡事件循环。

        原为同步方法，被 ``_collect_runtimes`` 循环调用；node_modules
        / venv 单个环境几万到几十万文件 × 100 环境 → 数百万 stat 全串在
        事件循环上，一次 GC 冻住 worker 数十秒（心跳/日志/poll 全停）。
        """
        return await asyncio.to_thread(self._get_dir_size_sync, path)

    def _get_disk_usage(self) -> float:
        """获取磁盘使用率"""
        try:
            stat = os.statvfs(self.venvs_dir)
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bavail * stat.f_frsize
            used = total - free
            return used / total if total > 0 else 0
        except Exception:
            return 0

    def _load_manifest(self, venv_path: str) -> dict[str, Any] | None:
        """加载清单文件"""
        manifest_path = os.path.join(venv_path, "manifest.json")
        if not os.path.exists(manifest_path):
            return None

        try:
            with open(manifest_path, encoding="utf-8") as f:
                return ujson.load(f)
        except Exception:
            return None

    @staticmethod
    def _is_managed_runtime(name: str, manifest: dict[str, Any] | None) -> bool:
        """仅回收 RuntimeBuilder 创建的哈希环境，命名环境由 UVManager 管理。"""
        return manifest is not None and manifest.get("runtime_hash") == name

    async def _collect_runtimes(self) -> list[RuntimeInfo]:
        """收集所有运行时信息"""
        if not os.path.exists(self.venvs_dir):
            return []

        runtimes: list[RuntimeInfo] = []
        for name in os.listdir(self.venvs_dir):
            if runtime := await self._collect_runtime(name):
                runtimes.append(runtime)

        return runtimes

    async def _collect_runtime(self, name: str) -> RuntimeInfo | None:
        venv_path = os.path.join(self.venvs_dir, name)
        if not os.path.isdir(venv_path):
            return None
        python_exe = os.path.join(
            venv_path,
            "Scripts" if os.name == "nt" else "bin",
            "python.exe" if os.name == "nt" else "python",
        )
        if not os.path.exists(python_exe):
            return None
        manifest = self._load_manifest(venv_path)
        if manifest is None or not self._is_managed_runtime(name, manifest):
            return None
        return RuntimeInfo(
            runtime_hash=name,
            path=venv_path,
            size_bytes=await self._get_dir_size(venv_path),
            created_at=self._runtime_timestamp(venv_path, manifest, key="created_at", fallback=os.path.getctime),
            last_used_at=self._runtime_timestamp(venv_path, manifest, key="last_used", fallback=os.path.getmtime),
        )

    @staticmethod
    def _runtime_timestamp(
        path: str,
        manifest: dict[str, Any],
        *,
        key: str,
        fallback: Callable[[str], float],
    ) -> datetime | None:
        if value := manifest.get(key):
            with contextlib.suppress(ValueError, TypeError):
                return datetime.fromisoformat(value)
        with contextlib.suppress(OSError):
            return datetime.fromtimestamp(fallback(path))
        return None

    async def _apply_ttl_policy(
        self,
        runtimes: list[RuntimeInfo],
    ) -> list[RuntimeInfo]:
        """
        应用 TTL 策略

        返回需要清理的运行时列表
        """
        if self.policy.ttl_seconds <= 0:
            return []

        now = datetime.now()
        cutoff = now.timestamp() - self.policy.ttl_seconds
        to_clean: list[RuntimeInfo] = []

        for rt in runtimes:
            last_used = rt.last_used_at or rt.created_at
            if last_used and last_used.timestamp() < cutoff:
                to_clean.append(rt)
                logger.debug(f"TTL 过期: {rt.runtime_hash}")

        return to_clean

    async def _apply_lru_policy(
        self,
        runtimes: list[RuntimeInfo],
        already_marked: set[str],
    ) -> list[RuntimeInfo]:
        """
        应用 LRU 策略

        返回需要清理的运行时列表
        """
        if self.policy.max_count <= 0:
            return []

        # 过滤已标记的
        remaining = [rt for rt in runtimes if rt.runtime_hash not in already_marked]

        # 如果数量未超限，不清理
        if len(remaining) <= self.policy.max_count:
            return []

        # 按最后使用时间排序（最旧的在前）
        remaining.sort(key=lambda x: x.last_used_at or x.created_at or datetime.min)

        # 计算需要清理的数量
        to_clean_count = len(remaining) - self.policy.max_count

        # 保留最小数量
        to_clean_count = min(to_clean_count, len(remaining) - self.policy.min_keep)

        if to_clean_count <= 0:
            return []

        to_clean = remaining[:to_clean_count]
        for rt in to_clean:
            logger.debug(f"LRU 淘汰: {rt.runtime_hash}")

        return to_clean

    async def _apply_disk_watermark_policy(
        self,
        runtimes: list[RuntimeInfo],
        already_marked: set[str],
    ) -> list[RuntimeInfo]:
        """
        应用磁盘水位策略

        返回需要清理的运行时列表
        """
        disk_usage = self._get_disk_usage()

        if disk_usage < self.policy.disk_high_watermark:
            return []

        logger.warning(f"磁盘使用率 {disk_usage:.1%} 超过高水位 {self.policy.disk_high_watermark:.1%}")

        # 过滤已标记的
        remaining = [rt for rt in runtimes if rt.runtime_hash not in already_marked]

        # 按最后使用时间排序（最旧的在前）
        remaining.sort(key=lambda x: x.last_used_at or x.created_at or datetime.min)

        to_clean: list[RuntimeInfo] = []
        current_usage = disk_usage

        for rt in remaining:
            if current_usage <= self.policy.disk_low_watermark:
                break

            # 保留最小数量
            if len(remaining) - len(to_clean) <= self.policy.min_keep:
                break

            to_clean.append(rt)
            # 估算清理后的使用率
            # 这是一个近似值，实际效果取决于文件系统
            total_size = self._get_total_disk_size()
            if total_size > 0:
                current_usage -= rt.size_bytes / total_size

            logger.debug(f"磁盘水位清理: {rt.runtime_hash}")

        return to_clean

    def _get_total_disk_size(self) -> int:
        """获取磁盘总大小"""
        try:
            stat = os.statvfs(self.venvs_dir)
            return stat.f_blocks * stat.f_frsize
        except Exception:
            return 0

    async def _clean_runtime(self, runtime: RuntimeInfo) -> bool:
        """清理单个运行时。"""
        # in-use 保护：如果 RuntimeManager 报告该 runtime 仍在被任务持有，跳过。
        # 少数场景（如策略强制）可通过 RuntimeManager.remove(force=True) 绕过。
        if self._in_use_check is not None:
            try:
                if self._in_use_check(runtime.runtime_hash):
                    logger.info("运行时正在使用中，GC 跳过: {}", runtime.runtime_hash)
                    return False
            except Exception as exc:
                # 检查失败偏保守：不清理，避免误删
                logger.warning("in_use_check 异常，保守跳过 {}: {}", runtime.runtime_hash, exc)
                return False
        try:
            if self._cleanup_handler is not None:
                return await self._cleanup_handler(runtime.runtime_hash)
            # G1: 走线程池，避免 rmtree 卡事件循环
            await asyncio.to_thread(shutil.rmtree, runtime.path)
            logger.info(f"已清理运行时: {runtime.runtime_hash}")
            return True
        except Exception as e:
            logger.error(f"清理运行时失败 {runtime.runtime_hash}: {e}")
            self._stats.errors.append(f"清理 {runtime.runtime_hash} 失败: {e}")
            return False

    async def run_gc(self) -> GCRunResult:
        """
        执行一次垃圾回收

        Returns:
            GC 结果
        """
        result: GCRunResult = {
            "cleaned": 0,
            "bytes_freed": 0,
            "errors": [],
        }

        # 收集运行时信息
        runtimes = await self._collect_runtimes()

        if not runtimes:
            return result

        # 标记需要清理的运行时
        to_clean: set[str] = set()
        to_clean_list: list[RuntimeInfo] = []

        # 应用 TTL 策略
        ttl_clean = await self._apply_ttl_policy(runtimes)
        for rt in ttl_clean:
            if rt.runtime_hash not in to_clean:
                to_clean.add(rt.runtime_hash)
                to_clean_list.append(rt)

        # 应用 LRU 策略
        lru_clean = await self._apply_lru_policy(runtimes, to_clean)
        for rt in lru_clean:
            if rt.runtime_hash not in to_clean:
                to_clean.add(rt.runtime_hash)
                to_clean_list.append(rt)

        # 应用磁盘水位策略
        disk_clean = await self._apply_disk_watermark_policy(runtimes, to_clean)
        for rt in disk_clean:
            if rt.runtime_hash not in to_clean:
                to_clean.add(rt.runtime_hash)
                to_clean_list.append(rt)

        # 执行清理
        for rt in to_clean_list:
            if await self._clean_runtime(rt):
                result["cleaned"] += 1
                result["bytes_freed"] += rt.size_bytes

        # 更新统计
        self._stats.last_gc_time = datetime.now()
        self._stats.total_gc_runs += 1
        self._stats.total_cleaned += result["cleaned"]
        self._stats.total_bytes_freed += result["bytes_freed"]
        self._stats.last_cleaned = result["cleaned"]
        self._stats.last_bytes_freed = result["bytes_freed"]

        return result

    async def clean_by_hash(self, runtime_hash: str) -> bool:
        """按哈希清理指定运行时。"""
        venv_path = os.path.join(self.venvs_dir, runtime_hash)

        if not os.path.exists(venv_path):
            return False

        manifest = self._load_manifest(venv_path)
        if not self._is_managed_runtime(runtime_hash, manifest):
            return False

        try:
            size = await self._get_dir_size(venv_path)
            if self._cleanup_handler is not None:
                if not await self._cleanup_handler(runtime_hash):
                    return False
            else:
                # G1: rmtree 也走线程池；一个 node_modules 秒级删。
                await asyncio.to_thread(shutil.rmtree, venv_path)

            self._stats.total_cleaned += 1
            self._stats.total_bytes_freed += size

            logger.info(f"已清理运行时: {runtime_hash}")
            return True
        except Exception as e:
            logger.error(f"清理运行时失败 {runtime_hash}: {e}")
            return False

    def get_runtime_count(self) -> int:
        """获取运行时数量"""
        if not os.path.exists(self.venvs_dir):
            return 0

        count = 0
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if os.path.isdir(venv_path):
                python_exe = os.path.join(venv_path, "bin", "python")
                if os.name == "nt":
                    python_exe = os.path.join(venv_path, "Scripts", "python.exe")
                if os.path.exists(python_exe):
                    count += 1

        return count

    def get_total_size(self) -> int:
        """获取所有运行时的总大小（同步，用于 sync callers 如 stats dict）。

        G1: 这里保留同步以兼容 ``RuntimeManager.get_stats()``；内部走
        sync 版避免在 sync 上下文里跑事件循环调度。async 上下文请自行
        用 ``asyncio.to_thread(self.get_total_size)``。
        """
        if not os.path.exists(self.venvs_dir):
            return 0
        return self._get_dir_size_sync(self.venvs_dir)


# 全局 GC 实例
_runtime_gc: RuntimeGC | None = None


def get_runtime_gc(venvs_dir: str, policy: GCPolicy | None = None) -> RuntimeGC:
    """获取全局运行时 GC"""
    global _runtime_gc
    if _runtime_gc is None or _runtime_gc.venvs_dir != venvs_dir:
        _runtime_gc = RuntimeGC(venvs_dir, policy)
    return _runtime_gc
