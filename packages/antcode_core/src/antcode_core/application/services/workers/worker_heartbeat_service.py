"""节点心跳检测服务 - 智能心跳检测与状态管理

从 worker_service.py 拆分，专注于心跳检测相关功能。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.serialization import from_json
from antcode_core.domain.models import Worker, WorkerHeartbeat, WorkerStatus


class WorkerHeartbeatService:
    """节点心跳检测服务"""

    # 智能心跳检测配置
    HEARTBEAT_INTERVAL_ONLINE = settings.WORKER_HEARTBEAT_INTERVAL_ONLINE
    HEARTBEAT_INTERVAL_OFFLINE = settings.WORKER_HEARTBEAT_INTERVAL_OFFLINE
    HEARTBEAT_MAX_FAILURES = settings.WORKER_HEARTBEAT_MAX_FAILURES
    HEARTBEAT_TIMEOUT_REQUEST = settings.WORKER_HEARTBEAT_TIMEOUT_REQUEST
    HEARTBEAT_TIMEOUT = settings.WORKER_HEARTBEAT_TIMEOUT

    def __init__(self):
        """初始化心跳检测服务"""
        # 节点缓存：{worker_id: worker_object}
        self._worker_cache: dict[int, Worker] = {}

        # 节点状态：{worker_id: {'failures': int, 'next_check': datetime, 'suspended': bool}}
        self._worker_states: dict[int, dict] = {}

        # 缓存更新时间
        self._cache_updated_at: datetime | None = None

        # 缓存有效期（秒）
        self._cache_ttl = 300  # 5分钟

    @staticmethod
    def _normalize_status_value(status_value: WorkerStatus | str | None) -> str:
        if status_value is None:
            return WorkerStatus.ONLINE.value
        if isinstance(status_value, WorkerStatus):
            return status_value.value
        if isinstance(status_value, str):
            normalized = status_value.strip().lower()
            if normalized in (
                WorkerStatus.ONLINE.value,
                WorkerStatus.OFFLINE.value,
                WorkerStatus.CONNECTING.value,
                WorkerStatus.MAINTENANCE.value,
            ):
                return normalized
            if normalized == "stopped":
                return WorkerStatus.OFFLINE.value
        return WorkerStatus.ONLINE.value

    async def init_heartbeat_cache(self):
        """初始化心跳检测缓存"""
        try:
            workers = await Worker.all()
            now = datetime.now()

            self._worker_cache.clear()
            self._worker_states.clear()

            for worker in workers:
                self._worker_cache[worker.id] = worker
                self._worker_states[worker.id] = {
                    "failures": 0,
                    "next_check": now,  # 立即检测
                    "suspended": False,
                    "last_connect_attempt": None,
                }

            self._cache_updated_at = now
            logger.info(f"心跳检测缓存已初始化，共 {len(workers)} 个节点")
        except Exception as e:
            logger.error(f"初始化心跳缓存失败: {e}")

    async def refresh_worker_cache(self, force: bool = False):
        """刷新节点缓存（如果过期）"""
        now = datetime.now()

        # 如果缓存不存在或已过期，重新加载
        if force or (
            not self._cache_updated_at
            or (now - self._cache_updated_at).total_seconds() > self._cache_ttl
        ):
            workers = await Worker.all()

            # 更新现有节点，添加新节点
            for worker in workers:
                if worker.id not in self._worker_cache:
                    # 新节点
                    self._worker_cache[worker.id] = worker
                    self._worker_states[worker.id] = {
                        "failures": 0,
                        "next_check": now,
                        "suspended": False,
                        "last_connect_attempt": None,
                    }
                else:
                    # 更新现有节点
                    self._worker_cache[worker.id] = worker
                    if "last_connect_attempt" not in self._worker_states[worker.id]:
                        self._worker_states[worker.id]["last_connect_attempt"] = None

            # 移除已删除的节点
            cached_ids = set(self._worker_cache.keys())
            current_ids = {n.id for n in workers}
            deleted_ids = cached_ids - current_ids

            for worker_id in deleted_ids:
                del self._worker_cache[worker_id]
                del self._worker_states[worker_id]

            self._cache_updated_at = now

    async def smart_health_check(self) -> dict:
        """
        智能心跳检测（使用缓存和自适应间隔）
        - 在线节点每3秒检测
        - 离线节点逐渐延长间隔（最长60秒）
        - 失败达到阈值后进入低频检测或暂停
        - 手动测试成功后恢复自动检测
        """
        import time

        start_time = time.time()

        # 刷新缓存（如果需要）
        await self.refresh_worker_cache()

        now = datetime.now()
        results = {
            "total": len(self._worker_cache),
            "checked": 0,
            "skipped": 0,
            "online": 0,
            "offline": 0,
            "suspended": 0,
            "elapsed": 0.0,
        }

        # 并发检测所有需要检测的节点
        tasks = []

        for worker_id, worker in self._worker_cache.items():
            state = self._worker_states[worker_id]

            # 跳过已暂停检测的节点
            if state["suspended"]:
                results["suspended"] += 1
                continue

            # 检查是否到了检测时间
            if now >= state["next_check"]:
                tasks.append(self._check_single_worker(worker, state))
            else:
                results["skipped"] += 1

        # 并发执行检测
        if tasks:
            check_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in check_results:
                if isinstance(result, Exception):
                    logger.error(f"节点检测异常: {result}")
                    results["offline"] += 1
                else:
                    results["checked"] += 1
                    if result:
                        results["online"] += 1
                    else:
                        results["offline"] += 1

        results["elapsed"] = time.time() - start_time

        # 记录检测摘要（总是记录，便于调试）
        logger.debug(
            f"心跳检测: 总计{results['total']}, "
            f"检测{results['checked']}, 跳过{results['skipped']}, "
            f"在线{results['online']}, 离线{results['offline']}, "
            f"暂停{results['suspended']}, 耗时{results['elapsed']:.2f}s"
        )

        return results

    async def _get_redis_heartbeat(self, worker: Worker) -> datetime | None:
        """从 Redis 获取节点心跳时间（Direct 模式）"""
        try:
            from antcode_core.infrastructure.redis import get_redis_client
            from antcode_core.infrastructure.redis import decode_stream_payload, worker_heartbeat_key

            redis = await get_redis_client()
            hb_key = worker_heartbeat_key(worker.public_id)
            raw = await redis.hgetall(hb_key)

            if not raw:
                return None

            # 解析心跳数据
            data = decode_stream_payload(raw)

            # 获取时间戳
            timestamp_str = data.get("timestamp")
            if not timestamp_str:
                return None

            # 解析 ISO 格式时间戳
            try:
                hb_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if hb_time.tzinfo is not None:
                    hb_time = hb_time.astimezone().replace(tzinfo=None)
                return hb_time
            except Exception as e:
                logger.debug(f"解析心跳时间戳失败: {timestamp_str}, error={e}")
                return None

        except Exception as e:
            logger.debug(f"从 Redis 获取心跳失败: worker={worker.name}, error={e}")
            return None

    async def _sync_redis_heartbeat_to_db(self, worker: Worker) -> bool:
        """将 Redis 心跳同步到数据库（Direct 模式）"""
        try:
            from antcode_core.infrastructure.redis import get_redis_client
            from antcode_core.infrastructure.redis import decode_stream_payload, worker_heartbeat_key

            redis = await get_redis_client()
            hb_key = worker_heartbeat_key(worker.public_id)
            raw = await redis.hgetall(hb_key)

            if not raw:
                return False

            # 解析心跳数据
            data = decode_stream_payload(raw)

            # 获取时间戳
            timestamp_str = data.get("timestamp")
            if not timestamp_str:
                return False

            # 解析时间戳
            try:
                hb_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                if hb_time.tzinfo is not None:
                    hb_time = hb_time.astimezone().replace(tzinfo=None)
            except Exception:
                return False

            # 检查是否需要更新（Redis 心跳比数据库新）
            db_hb = worker.last_heartbeat
            if db_hb is not None and db_hb.tzinfo is not None:
                db_hb = db_hb.astimezone().replace(tzinfo=None)

            if db_hb and hb_time <= db_hb:
                return False  # Redis 心跳不比数据库新

            # 更新数据库
            worker.last_heartbeat = hb_time
            worker.status = WorkerStatus.ONLINE.value

            # 更新指标
            metrics = {}
            if data.get("cpu_percent"):
                metrics["cpu"] = float(data["cpu_percent"])
            if data.get("memory_percent"):
                metrics["memory"] = float(data["memory_percent"])
            if data.get("disk_percent"):
                metrics["disk"] = float(data["disk_percent"])
            if data.get("running_tasks"):
                metrics["runningTasks"] = int(data["running_tasks"])
            if data.get("max_concurrent_tasks"):
                metrics["maxConcurrentTasks"] = int(data["max_concurrent_tasks"])

            if metrics:
                current_metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
                current_metrics.update(metrics)
                worker.metrics = current_metrics

            # 同步节点信息与能力（网关心跳写入）
            if data.get("version"):
                worker.version = data["version"]
            if data.get("os_type"):
                worker.os_type = data["os_type"]
            if data.get("os_version"):
                worker.os_version = data["os_version"]
            if data.get("python_version"):
                worker.python_version = data["python_version"]
            if data.get("machine_arch"):
                worker.machine_arch = data["machine_arch"]
            if data.get("capabilities"):
                try:
                    import json

                    capabilities = json.loads(data["capabilities"])
                    if isinstance(capabilities, dict):
                        worker.capabilities = capabilities
                except Exception:
                    pass

            await worker.save()
            logger.debug(f"已同步 Redis 心跳到数据库: worker={worker.name}, time={hb_time}")
            return True

        except Exception as e:
            logger.debug(f"同步 Redis 心跳失败: worker={worker.name}, error={e}")
            return False

    async def _check_single_worker(self, worker: Worker, state: dict) -> bool:
        """
        检测单个节点
        返回：True=在线, False=离线

        检测顺序：
        1. 检查数据库中的 last_heartbeat
        2. 如果数据库心跳过期，尝试从 Redis 获取心跳（Direct 模式）
        3. 如果 Redis 有新心跳，同步到数据库
        """
        old_status = worker.status
        now = datetime.now()
        last_hb = worker.last_heartbeat

        if last_hb is not None and last_hb.tzinfo is not None:
            last_hb = last_hb.astimezone().replace(tzinfo=None)

        if last_hb and (now - last_hb).total_seconds() <= self.HEARTBEAT_TIMEOUT:
            redis_hb = await self._get_redis_heartbeat(worker)
            if redis_hb is not None:
                if redis_hb.tzinfo is not None:
                    redis_hb = redis_hb.astimezone().replace(tzinfo=None)
                if not last_hb or redis_hb > last_hb:
                    await self._sync_redis_heartbeat_to_db(worker)
            state["failures"] = 0
            state["next_check"] = now + timedelta(seconds=self.HEARTBEAT_INTERVAL_ONLINE)
            if old_status != WorkerStatus.ONLINE:
                latest = await self._refresh_worker_from_db(worker.id)
                if latest:
                    worker = latest
                worker.status = WorkerStatus.ONLINE.value
                await worker.save()
                logger.info(f"节点 {worker.name} 恢复在线")
            return True

        # 数据库心跳过期，尝试从 Redis 获取（Direct 模式支持）
        redis_hb = await self._get_redis_heartbeat(worker)
        if redis_hb and (now - redis_hb).total_seconds() <= self.HEARTBEAT_TIMEOUT:
            # Redis 有有效心跳，同步到数据库
            await self._sync_redis_heartbeat_to_db(worker)
            state["failures"] = 0
            state["next_check"] = now + timedelta(seconds=self.HEARTBEAT_INTERVAL_ONLINE)
            if old_status != WorkerStatus.ONLINE:
                logger.info(f"节点 {worker.name} 恢复在线（从 Redis 同步）")
            return True

        # 使用最新数据库记录再次确认，避免缓存过期导致误判
        latest = await self._refresh_worker_from_db(worker.id)
        if latest:
            last_hb = latest.last_heartbeat
            if last_hb is not None and last_hb.tzinfo is not None:
                last_hb = last_hb.astimezone().replace(tzinfo=None)
            if last_hb and (now - last_hb).total_seconds() <= self.HEARTBEAT_TIMEOUT:
                latest.status = WorkerStatus.ONLINE.value
                state["failures"] = 0
                state["next_check"] = now + timedelta(seconds=self.HEARTBEAT_INTERVAL_ONLINE)
                await latest.save()
                if old_status != WorkerStatus.ONLINE:
                    logger.info(f"节点 {latest.name} 恢复在线")
                return True
            worker = latest

        await self._handle_worker_offline(worker, state, old_status)
        return False

    async def _handle_worker_offline(
        self,
        worker: Worker,
        state: dict,
        old_status: WorkerStatus | str,
    ):
        """处理节点离线"""
        worker.status = WorkerStatus.OFFLINE.value
        state["failures"] += 1

        # 根据失败次数调整检测间隔
        if state["failures"] >= self.HEARTBEAT_MAX_FAILURES:
            if worker.api_key and worker.secret_key:
                state["suspended"] = False
                state["next_check"] = datetime.now() + timedelta(
                    seconds=self.HEARTBEAT_INTERVAL_OFFLINE
                )
                # 只在首次达到最大失败次数时记录警告
                if state["failures"] == self.HEARTBEAT_MAX_FAILURES:
                    logger.warning(
                        f"节点 {worker.name} 连续失败 {state['failures']} 次，保持低频检测等待自动重连"
                    )
            else:
                # 暂停自动检测
                state["suspended"] = True
                logger.warning(
                    f"节点 {worker.name} 连续失败 {state['failures']} 次，已暂停自动检测，等待手动测试"
                )
        else:
            # 逐渐延长检测间隔（指数退避）
            interval = min(
                self.HEARTBEAT_INTERVAL_ONLINE * (2 ** state["failures"]),
                self.HEARTBEAT_INTERVAL_OFFLINE,
            )
            state["next_check"] = datetime.now() + timedelta(seconds=interval)

            logger.debug(
                f"节点 {worker.name} 离线（失败{state['failures']}次），下次检测间隔: {interval}秒"
            )

        # 状态变化时保存到数据库
        if old_status != WorkerStatus.OFFLINE:
            await worker.save()
            logger.warning(f"节点 {worker.name} 离线")

    async def manual_test_worker(self, worker_id: int) -> bool:
        """
        手动测试节点连接
        如果成功，恢复自动心跳检测
        """
        # 强制刷新缓存，确保新节点被加入
        self._cache_updated_at = None
        await self.refresh_worker_cache()

        if worker_id not in self._worker_cache:
            # 如果仍然不在缓存中，尝试直接从数据库获取并添加到缓存
            worker = await Worker.filter(id=worker_id).first()
            if not worker:
                logger.error(f"节点 {worker_id} 不存在")
                return False

            # 添加到缓存
            self._worker_cache[worker_id] = worker
            self._worker_states[worker_id] = {
                "failures": 0,
                "next_check": datetime.now(),
                "suspended": False,
                "last_connect_attempt": None,
            }

        worker = self._worker_cache[worker_id]
        state = self._worker_states[worker_id]

        # 执行检测
        is_online = await self._check_single_worker(worker, state)

        # 如果成功，恢复自动检测
        if is_online:
            state["suspended"] = False
            state["failures"] = 0
            state["next_check"] = datetime.now() + timedelta(seconds=self.HEARTBEAT_INTERVAL_ONLINE)
            logger.info(f"节点 {worker.name} 手动测试成功，已恢复自动心跳检测")

        return is_online

    async def check_all_workers_health(self) -> dict:
        """检查所有节点健康状态"""
        return await self.smart_health_check()

    async def check_offline_workers(self, workers: list[Worker]):
        """检查并更新离线节点"""
        # 使用本地时间（naive datetime）避免时区问题
        now = datetime.now()
        timeout = timedelta(seconds=self.HEARTBEAT_TIMEOUT)

        for worker in workers:
            if worker.status == WorkerStatus.ONLINE and worker.last_heartbeat:
                # 将心跳时间转换为 naive datetime（去掉时区信息）
                last_hb = worker.last_heartbeat
                if last_hb.tzinfo is not None:
                    # 如果有时区信息，转换为本地时间再去掉时区
                    last_hb = last_hb.astimezone().replace(tzinfo=None)

                time_diff = now - last_hb
                if time_diff > timeout:
                    logger.info(
                        f"节点 {worker.name} 心跳超时 ({time_diff.total_seconds():.0f}秒 > {self.HEARTBEAT_TIMEOUT}秒)，标记为离线"
                    )
                    worker.status = WorkerStatus.OFFLINE.value
                    await worker.save()

    async def update_heartbeat(
        self,
        worker_id: str,
        status: str | None = None,
        cpu: float | None = None,
        memory: float | None = None,
        disk: float | None = None,
        running_tasks: int | None = None,
        max_concurrent_tasks: int | None = None,
        version: str | None = None,
        os_type: str | None = None,
        os_version: str | None = None,
        python_version: str | None = None,
        machine_arch: str | None = None,
        capabilities: dict | None = None,
    ) -> bool:
        """通过 worker_id 更新心跳（供 Gateway 调用）"""
        worker = await Worker.filter(public_id=worker_id).first()
        if not worker:
            return False

        status_value = self._normalize_status_value(status)

        metrics: dict = {}
        if cpu is not None:
            metrics["cpu"] = round(cpu, 1)
        if memory is not None:
            metrics["memory"] = round(memory, 1)
        if disk is not None:
            metrics["disk"] = round(disk, 1)
        if running_tasks is not None:
            metrics["runningTasks"] = running_tasks
        if max_concurrent_tasks is not None:
            metrics["maxConcurrentTasks"] = max_concurrent_tasks

        return await self.heartbeat(
            worker=worker,
            status_value=status_value,
            metrics=metrics if metrics else None,
            version=version,
            os_type=os_type,
            os_version=os_version,
            python_version=python_version,
            machine_arch=machine_arch,
            capabilities=capabilities,
        )

    async def heartbeat(
        self,
        worker: Worker,
        status_value: WorkerStatus | str | None = None,
        metrics: dict | None = None,
        version: str | None = None,
        os_type: str | None = None,
        os_version: str | None = None,
        python_version: str | None = None,
        machine_arch: str | None = None,
        capabilities: dict | None = None,
        spider_stats: dict | None = None,
    ) -> bool:
        """
        处理节点心跳

        Args:
            worker: 节点对象
            status_value: 节点状态
            metrics: 系统指标
            version: 节点版本
            os_type: 操作系统类型
            os_version: 操作系统版本
            python_version: Python 版本
            machine_arch: CPU 架构
            capabilities: 节点能力
            spider_stats: 爬虫统计摘要
        """
        status_value = self._normalize_status_value(status_value)

        # 更新节点状态
        worker.status = status_value
        worker.last_heartbeat = datetime.now()

        # 处理 metrics，合并爬虫统计
        if metrics:
            # 如果心跳中包含 spider_stats，合并到 metrics
            if spider_stats:
                metrics["spider_stats"] = spider_stats
            current_metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
            current_metrics.update(metrics)
            worker.metrics = current_metrics
        elif spider_stats:
            # 如果只有 spider_stats，更新现有 metrics
            current_metrics = worker.metrics if isinstance(worker.metrics, dict) else {}
            current_metrics["spider_stats"] = spider_stats
            worker.metrics = current_metrics

        if version:
            worker.version = version

        # 更新操作系统信息（如果提供）
        if os_type:
            worker.os_type = os_type
        if os_version:
            worker.os_version = os_version
        if python_version:
            worker.python_version = python_version
        if machine_arch:
            worker.machine_arch = machine_arch

        # 更新节点能力（如果提供）
        if capabilities:
            if not isinstance(capabilities, dict):
                logger.warning(f"capabilities 类型错误: {type(capabilities)}, 值: {capabilities}")
                capabilities = None
            else:
                capabilities = self._normalize_capabilities(capabilities)
                worker.capabilities = capabilities
                # 记录能力变更
                has_render = self._check_render_capability(capabilities)
                logger.info(f"节点 {worker.name} 能力更新: 渲染能力={has_render}")

        await worker.save()

        self._sync_cache_on_heartbeat(worker)

        # 记录心跳历史（包含爬虫统计）
        heartbeat_metrics = metrics.copy() if metrics else {}
        if spider_stats and "spider_stats" not in heartbeat_metrics:
            heartbeat_metrics["spider_stats"] = spider_stats

        await WorkerHeartbeat.create(
            worker_id=worker.id,
            status=status_value,
            metrics=heartbeat_metrics if heartbeat_metrics else None,
        )

        return True

    def _sync_cache_on_heartbeat(self, worker: Worker) -> None:
        """同步心跳到缓存，避免健康检查使用过期节点信息"""
        if worker.id not in self._worker_cache:
            return
        self._worker_cache[worker.id] = worker
        state = self._worker_states.get(worker.id)
        if not state:
            return
        state["failures"] = 0
        state["suspended"] = False
        state["next_check"] = datetime.now() + timedelta(seconds=self.HEARTBEAT_INTERVAL_ONLINE)

    async def _refresh_worker_from_db(self, worker_id: int) -> Worker | None:
        """按需刷新单节点，避免使用过期缓存覆盖新数据"""
        latest = await Worker.filter(id=worker_id).first()
        if not latest:
            return None
        self._worker_cache[worker_id] = latest
        return latest

    def _check_render_capability(self, capabilities: dict | None) -> bool:
        """检查节点是否有渲染能力"""
        if not capabilities:
            return False
        cap = capabilities.get("drissionpage")
        if isinstance(cap, str):
            try:
                cap = from_json(cap)
            except Exception:
                return False
        return bool(isinstance(cap, dict) and cap.get("enabled"))

    def _normalize_capabilities(self, capabilities: dict) -> dict:
        normalized: dict = {}
        for key, value in capabilities.items():
            if isinstance(value, str):
                raw = value.strip()
                if raw.startswith("{") or raw.startswith("["):
                    with contextlib.suppress(Exception):
                        value = from_json(raw)
                elif raw.lower() in {"true", "false"}:
                    value = raw.lower() == "true"
            normalized[key] = value
        return normalized


# 创建服务实例
worker_heartbeat_service = WorkerHeartbeatService()
