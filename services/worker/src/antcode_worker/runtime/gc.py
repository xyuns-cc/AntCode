"""
运行时垃圾回收

实现 TTL/LRU/disk watermark 清理策略。

Requirements: 6.6
"""

import asyncio
import contextlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import ujson
from loguru import logger


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
    disk_low_watermark: float = 0.70   # 低水位（停止清理）

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
    ):
        """
        初始化垃圾回收器

        Args:
            venvs_dir: 虚拟环境目录
            policy: 清理策略
        """
        self.venvs_dir = venvs_dir
        self.policy = policy or GCPolicy()
        self._stats = GCStats()
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_gc_complete: Callable[[GCStats], None] | None = None

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
                    f"运行时 GC 完成: cleaned={result['cleaned']}, "
                    f"freed={result['bytes_freed'] / 1024 / 1024:.2f}MB"
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

    def _get_dir_size(self, path: str) -> int:
        """获取目录大小"""
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

    async def _collect_runtimes(self) -> list[RuntimeInfo]:
        """收集所有运行时信息"""
        runtimes = []

        if not os.path.exists(self.venvs_dir):
            return runtimes

        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if not os.path.isdir(venv_path):
                continue

            # 检查是否是有效的虚拟环境
            python_exe = os.path.join(venv_path, "bin", "python")
            if os.name == "nt":
                python_exe = os.path.join(venv_path, "Scripts", "python.exe")

            if not os.path.exists(python_exe):
                continue

            # 加载清单
            manifest = self._load_manifest(venv_path)

            # 解析时间
            created_at = None
            last_used_at = None

            if manifest:
                if manifest.get("created_at"):
                    with contextlib.suppress(Exception):
                        created_at = datetime.fromisoformat(manifest["created_at"])
                if manifest.get("last_used"):
                    with contextlib.suppress(Exception):
                        last_used_at = datetime.fromisoformat(manifest["last_used"])

            # 如果没有时间信息，使用文件修改时间
            if not created_at:
                with contextlib.suppress(Exception):
                    created_at = datetime.fromtimestamp(os.path.getctime(venv_path))

            if not last_used_at:
                with contextlib.suppress(Exception):
                    last_used_at = datetime.fromtimestamp(os.path.getmtime(venv_path))

            runtimes.append(RuntimeInfo(
                runtime_hash=name,
                path=venv_path,
                size_bytes=self._get_dir_size(venv_path),
                created_at=created_at,
                last_used_at=last_used_at,
            ))

        return runtimes

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
        to_clean = []

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
        remaining.sort(key=lambda x: (x.last_used_at or x.created_at or datetime.min))

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
        remaining.sort(key=lambda x: (x.last_used_at or x.created_at or datetime.min))

        to_clean = []
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
        """
        清理单个运行时

        Args:
            runtime: 运行时信息

        Returns:
            是否成功清理
        """
        try:
            shutil.rmtree(runtime.path)
            logger.info(f"已清理运行时: {runtime.runtime_hash}")
            return True
        except Exception as e:
            logger.error(f"清理运行时失败 {runtime.runtime_hash}: {e}")
            self._stats.errors.append(f"清理 {runtime.runtime_hash} 失败: {e}")
            return False

    async def run_gc(self) -> dict[str, Any]:
        """
        执行一次垃圾回收

        Returns:
            GC 结果
        """
        result = {
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
        """
        按哈希清理指定运行时

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否成功清理
        """
        venv_path = os.path.join(self.venvs_dir, runtime_hash)

        if not os.path.exists(venv_path):
            return False

        try:
            size = self._get_dir_size(venv_path)
            shutil.rmtree(venv_path)

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
        """获取所有运行时的总大小"""
        if not os.path.exists(self.venvs_dir):
            return 0

        return self._get_dir_size(self.venvs_dir)


# 全局 GC 实例
_runtime_gc: RuntimeGC | None = None


def get_runtime_gc(venvs_dir: str, policy: GCPolicy | None = None) -> RuntimeGC:
    """获取全局运行时 GC"""
    global _runtime_gc
    if _runtime_gc is None or _runtime_gc.venvs_dir != venvs_dir:
        _runtime_gc = RuntimeGC(venvs_dir, policy)
    return _runtime_gc
