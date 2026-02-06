"""
缓存垃圾回收器

负责清理过期的虚拟环境、日志文件和临时文件。
"""

import asyncio
import contextlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import ujson
from loguru import logger


@dataclass
class GCConfig:
    """垃圾回收配置"""

    # 环境过期时间（秒），默认 7 天
    env_ttl: int = 7 * 24 * 3600
    # 日志过期时间（秒），默认 3 天
    log_ttl: int = 3 * 24 * 3600
    # 临时文件过期时间（秒），默认 1 天
    temp_ttl: int = 24 * 3600
    # GC 检查间隔（秒），默认 1 小时
    check_interval: int = 3600
    # 最大保留环境数量
    max_envs: int = 100
    # 最大保留日志目录数量
    max_log_dirs: int = 1000
    # 是否启用自动 GC
    auto_gc: bool = True


class CacheGC:
    """
    缓存垃圾回收器

    定期清理过期的资源：
    - 虚拟环境：超过 TTL 未使用的环境
    - 日志文件：超过 TTL 的执行日志
    - 临时文件：超过 TTL 的临时文件
    """

    def __init__(
        self,
        venvs_dir: str | None = None,
        logs_dir: str | None = None,
        temp_dir: str | None = None,
        config: GCConfig | None = None,
    ):
        self.venvs_dir = venvs_dir
        self.logs_dir = logs_dir
        self.temp_dir = temp_dir
        self._config = config or GCConfig()
        self._running = False
        self._task: asyncio.Task | None = None
        self._on_gc_complete: Callable[[dict], None] | None = None

        # 统计信息
        self._stats = {
            "last_gc_time": None,
            "envs_cleaned": 0,
            "logs_cleaned": 0,
            "temp_cleaned": 0,
            "bytes_freed": 0,
        }

    @property
    def config(self) -> GCConfig:
        """获取配置"""
        return self._config

    @property
    def stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()

    def set_gc_callback(self, callback: Callable[[dict], None]) -> None:
        """设置 GC 完成回调"""
        self._on_gc_complete = callback

    async def start(self) -> None:
        """启动自动 GC"""
        if self._running:
            return

        if not self._config.auto_gc:
            logger.info("自动 GC 已禁用")
            return

        self._running = True
        self._task = asyncio.create_task(self._gc_loop())
        logger.info(
            f"缓存 GC 已启动，检查间隔: {self._config.check_interval}s"
        )

    async def stop(self) -> None:
        """停止自动 GC"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        logger.info("缓存 GC 已停止")

    async def _gc_loop(self) -> None:
        """GC 循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.check_interval)
                if not self._running:
                    break

                result = await self.run_gc()
                logger.info(
                    f"GC 完成: envs={result['envs_cleaned']}, "
                    f"logs={result['logs_cleaned']}, "
                    f"temp={result['temp_cleaned']}, "
                    f"freed={result['bytes_freed'] / 1024 / 1024:.2f}MB"
                )

                if self._on_gc_complete:
                    try:
                        self._on_gc_complete(result)
                    except Exception as e:
                        logger.error(f"GC 回调异常: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GC 循环异常: {e}")
                await asyncio.sleep(60)

    async def run_gc(self) -> dict:
        """
        执行一次 GC

        Returns:
            GC 结果统计
        """
        result = {
            "envs_cleaned": 0,
            "logs_cleaned": 0,
            "temp_cleaned": 0,
            "bytes_freed": 0,
            "errors": [],
        }

        # 清理虚拟环境
        if self.venvs_dir:
            env_result = await self._gc_envs()
            result["envs_cleaned"] = env_result["cleaned"]
            result["bytes_freed"] += env_result["bytes_freed"]
            result["errors"].extend(env_result.get("errors", []))

        # 清理日志
        if self.logs_dir:
            log_result = await self._gc_logs()
            result["logs_cleaned"] = log_result["cleaned"]
            result["bytes_freed"] += log_result["bytes_freed"]
            result["errors"].extend(log_result.get("errors", []))

        # 清理临时文件
        if self.temp_dir:
            temp_result = await self._gc_temp()
            result["temp_cleaned"] = temp_result["cleaned"]
            result["bytes_freed"] += temp_result["bytes_freed"]
            result["errors"].extend(temp_result.get("errors", []))

        # 更新统计
        self._stats["last_gc_time"] = datetime.now().isoformat()
        self._stats["envs_cleaned"] += result["envs_cleaned"]
        self._stats["logs_cleaned"] += result["logs_cleaned"]
        self._stats["temp_cleaned"] += result["temp_cleaned"]
        self._stats["bytes_freed"] += result["bytes_freed"]

        return result

    async def _gc_envs(self) -> dict:
        """清理过期虚拟环境"""
        result = {"cleaned": 0, "bytes_freed": 0, "errors": []}

        if not self.venvs_dir or not os.path.exists(self.venvs_dir):
            return result

        now = time.time()
        cutoff = now - self._config.env_ttl
        envs_info = []

        # 收集环境信息
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if not os.path.isdir(venv_path):
                continue

            manifest_path = os.path.join(venv_path, "manifest.json")
            last_used = 0

            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest = ujson.load(f)
                    last_used_str = manifest.get("last_used") or manifest.get(
                        "created_at"
                    )
                    if last_used_str:
                        last_used = datetime.fromisoformat(
                            last_used_str
                        ).timestamp()
                except Exception:
                    pass

            if last_used == 0:
                last_used = os.path.getmtime(venv_path)

            envs_info.append(
                {
                    "name": name,
                    "path": venv_path,
                    "last_used": last_used,
                    "size": self._get_dir_size(venv_path),
                }
            )

        # 按最后使用时间排序
        envs_info.sort(key=lambda x: x["last_used"])

        # 清理过期环境
        for env in envs_info:
            should_clean = False

            # 超过 TTL
            if env["last_used"] < cutoff:
                should_clean = True
                logger.debug(f"环境 {env['name']} 超过 TTL，将被清理")

            # 超过最大数量限制
            if (
                len(envs_info) - result["cleaned"] > self._config.max_envs
                and not should_clean
            ):
                should_clean = True
                logger.debug(f"环境 {env['name']} 超过数量限制，将被清理")

            if should_clean:
                try:
                    shutil.rmtree(env["path"])
                    result["cleaned"] += 1
                    result["bytes_freed"] += env["size"]
                    logger.info(f"已清理环境: {env['name']}")
                except Exception as e:
                    result["errors"].append(f"清理环境 {env['name']} 失败: {e}")
                    logger.error(f"清理环境 {env['name']} 失败: {e}")

        return result

    async def _gc_logs(self) -> dict:
        """清理过期日志"""
        result = {"cleaned": 0, "bytes_freed": 0, "errors": []}

        if not self.logs_dir or not os.path.exists(self.logs_dir):
            return result

        now = time.time()
        cutoff = now - self._config.log_ttl
        logs_info = []

        # 收集日志目录信息
        for name in os.listdir(self.logs_dir):
            log_path = os.path.join(self.logs_dir, name)
            if not os.path.isdir(log_path):
                continue

            mtime = os.path.getmtime(log_path)
            logs_info.append(
                {
                    "name": name,
                    "path": log_path,
                    "mtime": mtime,
                    "size": self._get_dir_size(log_path),
                }
            )

        # 按修改时间排序
        logs_info.sort(key=lambda x: x["mtime"])

        # 清理过期日志
        for log in logs_info:
            should_clean = False

            # 超过 TTL
            if log["mtime"] < cutoff:
                should_clean = True

            # 超过最大数量限制
            if (
                len(logs_info) - result["cleaned"] > self._config.max_log_dirs
                and not should_clean
            ):
                should_clean = True

            if should_clean:
                try:
                    shutil.rmtree(log["path"])
                    result["cleaned"] += 1
                    result["bytes_freed"] += log["size"]
                except Exception as e:
                    result["errors"].append(f"清理日志 {log['name']} 失败: {e}")
                    logger.error(f"清理日志 {log['name']} 失败: {e}")

        return result

    async def _gc_temp(self) -> dict:
        """清理临时文件"""
        result = {"cleaned": 0, "bytes_freed": 0, "errors": []}

        if not self.temp_dir or not os.path.exists(self.temp_dir):
            return result

        now = time.time()
        cutoff = now - self._config.temp_ttl

        for item in os.listdir(self.temp_dir):
            item_path = os.path.join(self.temp_dir, item)
            try:
                mtime = os.path.getmtime(item_path)
                if mtime < cutoff:
                    size = (
                        self._get_dir_size(item_path)
                        if os.path.isdir(item_path)
                        else os.path.getsize(item_path)
                    )

                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)

                    result["cleaned"] += 1
                    result["bytes_freed"] += size
            except Exception as e:
                result["errors"].append(f"清理临时文件 {item} 失败: {e}")

        return result

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


# 全局实例
cache_gc = CacheGC()
