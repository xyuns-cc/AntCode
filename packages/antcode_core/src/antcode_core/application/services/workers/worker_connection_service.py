"""Worker 连接服务 - Worker 注册与凭证管理

从 worker_service.py 拆分，专注于 Worker 注册/凭证/状态管理。

通信方式（Worker 主动连接架构）：
- Worker 通过 Gateway/Redis 主动心跳与拉取任务
- 控制平面仅负责发放凭证与记录 Worker 状态
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from loguru import logger

from antcode_core.common.security.api_key import store_api_key, store_secret_key
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.schemas.worker import WorkerRegisterDirectRequest

MAX_WORKER_NAME_LENGTH = 100
RANDOM_NAME_ATTEMPTS = 6
RANDOM_SUFFIX_BYTES = 3


@dataclass(frozen=True)
class _DirectWorkerIdentity:
    worker_id: str
    name: str
    host: str | None
    port: int | None


class WorkerConnectionService:
    """Worker 连接服务

    连接流程（Worker 主动连接）：
    1. 控制平面生成一次性安装 Key
    2. Worker 使用 Key 注册并获取凭证
    3. Worker 使用凭证主动连接 Gateway/Redis
    4. 通过心跳更新 Worker 在线状态
    """

    def normalize_address(self, host, port):
        """规范化 Worker 地址与端口"""
        raw = (host or "").strip()
        if not raw:
            return host, port

        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        normalized_host = parsed.hostname or raw
        normalized_port = parsed.port or port
        return normalized_host, normalized_port

    async def register_worker(self, request) -> tuple[Worker, str, str]:
        """Worker 自注册（通过心跳触发）"""
        host, port = self.normalize_address(request.host, request.port)

        existing = await Worker.filter(host=host, port=port).first()
        if existing:
            api_key = secrets.token_hex(32)
            secret_key = secrets.token_hex(64)
            existing.name = request.name
            existing.region = request.region
            existing.version = request.version
            # P1-33: MAINTENANCE 是运维态,重新注册不能把它翻回 ONLINE
            if existing.status != WorkerStatus.MAINTENANCE.value:
                existing.status = WorkerStatus.ONLINE.value
            existing.last_heartbeat = datetime.now()
            if request.metrics:
                existing.metrics = request.metrics.model_dump()
            store_api_key(existing, api_key)
            store_secret_key(existing, secret_key)
            await existing.save()

            from antcode_core.application.services.workers.worker_heartbeat_service import (
                worker_heartbeat_service,
            )

            await worker_heartbeat_service.refresh_worker_cache(force=True)

            return existing, api_key, secret_key

        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        worker = Worker(
            name=request.name,
            host=host,
            port=port,
            region=request.region,
            version=request.version,
            status=WorkerStatus.ONLINE.value,
            last_heartbeat=datetime.now(UTC),
            metrics=request.metrics.model_dump() if request.metrics else None,
            transport_mode="gateway",
        )
        store_api_key(worker, api_key)
        store_secret_key(worker, secret_key)
        await worker.save()

        logger.info(f"Worker 注册成功: {worker.name} ({worker.host}:{worker.port})")
        from antcode_core.application.services.workers.worker_heartbeat_service import (
            worker_heartbeat_service,
        )

        await worker_heartbeat_service.refresh_worker_cache(force=True)
        return worker, api_key, secret_key

    async def register_direct_worker(self, request: WorkerRegisterDirectRequest) -> tuple[Worker, bool]:
        """Direct Worker 注册（使用 worker_id 作为 public_id）"""
        worker_id = (request.worker_id or "").strip()
        if not worker_id:
            raise ValueError("worker_id 不能为空")

        address = self.normalize_address(request.host, request.port)
        worker = await Worker.filter(public_id=worker_id).first()
        if worker:
            return await self._update_direct_worker(worker, request, address), False

        name = await self._select_direct_worker_name(request.name, worker_id)
        identity = _DirectWorkerIdentity(worker_id, name, *address)
        worker = await self._create_direct_worker(request, identity)
        logger.info(f"Direct Worker 注册成功: {worker.name} ({worker.public_id})")
        return worker, True

    async def _update_direct_worker(
        self,
        worker: Worker,
        request: WorkerRegisterDirectRequest,
        address: tuple[str | None, int | None],
    ) -> Worker:
        requested_name = (request.name or "").strip()
        if requested_name and requested_name != worker.name:
            duplicate = await Worker.filter(name=requested_name).exclude(id=worker.id).exists()
            if not duplicate:
                worker.name = requested_name

        host, port = address
        truthy_values = {
            "host": host,
            "port": port,
            "version": request.version,
            "os_type": request.os_type,
            "os_version": request.os_version,
            "python_version": request.python_version,
            "machine_arch": request.machine_arch,
        }
        for field, value in truthy_values.items():
            if value:
                setattr(worker, field, value)
        if request.region is not None:
            worker.region = request.region
        if request.capabilities is not None:
            worker.capabilities = request.capabilities.model_dump()
        if worker.status != WorkerStatus.MAINTENANCE.value:
            worker.status = WorkerStatus.ONLINE.value
        worker.last_heartbeat = datetime.now(UTC)
        await worker.save()
        return worker

    async def _select_direct_worker_name(self, requested_name: str | None, worker_id: str) -> str:
        base_name = ((requested_name or "").strip() or worker_id)[:MAX_WORKER_NAME_LENGTH]
        candidates = (
            base_name,
            f"{base_name}-{worker_id[:6]}",
            f"{base_name}-{worker_id[-6:]}",
            f"worker-{worker_id[:12]}",
        )
        for candidate in candidates:
            normalized = candidate[:MAX_WORKER_NAME_LENGTH]
            if not await Worker.filter(name=normalized).exists():
                return normalized

        prefix = base_name[: MAX_WORKER_NAME_LENGTH - 8]
        for _ in range(RANDOM_NAME_ATTEMPTS):
            candidate = f"{prefix}-{secrets.token_hex(RANDOM_SUFFIX_BYTES)}"
            if not await Worker.filter(name=candidate).exists():
                return candidate
        raise ValueError("无法为 Worker 生成唯一名称")

    @staticmethod
    async def _create_direct_worker(
        request: WorkerRegisterDirectRequest,
        identity: _DirectWorkerIdentity,
    ) -> Worker:
        capabilities = request.capabilities.model_dump() if request.capabilities else {}

        return await Worker.create(
            public_id=identity.worker_id,
            name=identity.name,
            host=identity.host or "",
            port=identity.port or 0,
            region=request.region or "",
            version=request.version or None,
            status=WorkerStatus.ONLINE.value,
            last_heartbeat=datetime.now(UTC),
            os_type=request.os_type or None,
            os_version=request.os_version or None,
            python_version=request.python_version or None,
            machine_arch=request.machine_arch or None,
            capabilities=capabilities,
            transport_mode="direct",
        )

    async def disconnect_worker(self, worker: Worker) -> bool:
        """断开 Worker 连接（标记离线）"""
        worker.status = WorkerStatus.OFFLINE.value
        await worker.save()

        logger.info(f"Worker 已标记离线: {worker.name}")
        return True

    async def test_connection(self, worker: Worker) -> dict:
        """测试 Worker 在线状态（基于心跳）"""
        logger.info(f"测试 Worker 连接: {worker.name} (worker_id: {worker.public_id})")

        host, port = self.normalize_address(worker.host, worker.port)
        if host != worker.host or port != worker.port:
            worker.host = host
            worker.port = port
            await worker.save()

        if not worker.last_heartbeat:
            return {"success": False, "error": "无心跳记录"}

        now = datetime.now()
        last_hb = worker.last_heartbeat
        if last_hb.tzinfo is not None:
            last_hb = last_hb.astimezone().replace(tzinfo=None)

        latency_ms = int((now - last_hb).total_seconds() * 1000)
        if latency_ms > 0 and latency_ms <= 60000:
            return {
                "success": True,
                "latency": latency_ms,
                "connection_type": "heartbeat",
            }

        worker.status = WorkerStatus.OFFLINE.value
        await worker.save()
        return {"success": False, "error": "心跳超时"}

    async def refresh_worker_status(self, worker: Worker) -> Worker | None:
        """刷新 Worker 状态（基于心跳时间戳）

        P1-33: MAINTENANCE 是运维显式设置的目标态,心跳新鲜度检查不应覆盖它。
        只在原状态不是 MAINTENANCE 时才做 ONLINE/OFFLINE 判定。
        """
        if worker.status == WorkerStatus.MAINTENANCE.value:
            # 维护窗口内,只更新 last_heartbeat 的观测,不改 status
            await worker.save()
            return worker
        if not worker.last_heartbeat:
            worker.status = WorkerStatus.OFFLINE.value
        else:
            last_hb = worker.last_heartbeat
            if last_hb.tzinfo is not None:
                last_hb = last_hb.astimezone().replace(tzinfo=None)
            if (datetime.now() - last_hb).total_seconds() <= 60:
                worker.status = WorkerStatus.ONLINE.value
            else:
                worker.status = WorkerStatus.OFFLINE.value
        await worker.save()
        return worker

    async def init_worker_secrets(self):
        """启动时验证所有持久化 Worker 密钥可解密且哈希一致。"""
        from antcode_core.common.security.worker_auth import load_worker_secret

        workers = await Worker.filter(secret_key_encrypted__isnull=False).only("public_id")
        for worker in workers:
            secret = await load_worker_secret(worker.public_id)
            if secret is None:
                raise RuntimeError(f"Worker HMAC secret 不完整: {worker.public_id}")

        logger.info(f"已验证 {len(workers)} 个 Worker HMAC 密钥")

    async def get_worker_credentials(self, worker: Worker) -> dict:
        """凭据不可恢复，只允许注册响应返回一次。"""
        raise RuntimeError(f"Worker {worker.public_id} 凭据不可恢复")


# 创建服务实例
worker_connection_service = WorkerConnectionService()
