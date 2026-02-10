"""Worker 连接服务 - Worker 注册与凭证管理

从 worker_service.py 拆分，专注于 Worker 注册/凭证/状态管理。

通信方式（Worker 主动连接架构）：
- Worker 通过 Gateway/Redis 主动心跳与拉取任务
- 控制平面仅负责发放凭证与记录 Worker 状态
"""

import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse

from loguru import logger

from antcode_core.domain.models import Worker, WorkerStatus


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
        from antcode_core.common.security.worker_auth import worker_auth_verifier

        host, port = self.normalize_address(request.host, request.port)

        existing = await Worker.filter(host=host, port=port).first()
        if existing:
            existing.name = request.name
            existing.region = request.region
            existing.version = request.version
            existing.status = WorkerStatus.ONLINE.value
            existing.last_heartbeat = datetime.now()
            if request.metrics:
                existing.metrics = request.metrics.model_dump()
            await existing.save()

            if existing.secret_key:
                worker_auth_verifier.register_worker_secret(
                    existing.public_id, existing.secret_key
                )

            from antcode_core.application.services.workers.worker_heartbeat_service import (
                worker_heartbeat_service,
            )
            await worker_heartbeat_service.refresh_worker_cache(force=True)

            return existing, existing.api_key, existing.secret_key

        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        worker = await Worker.create(
            name=request.name,
            host=host,
            port=port,
            region=request.region,
            version=request.version,
            status=WorkerStatus.ONLINE.value,
            api_key=api_key,
            secret_key=secret_key,
            last_heartbeat=datetime.now(UTC),
            metrics=request.metrics.model_dump() if request.metrics else None,
            transport_mode="gateway",
        )

        worker_auth_verifier.register_worker_secret(worker.public_id, secret_key)
        logger.info(f"Worker 注册成功: {worker.name} ({worker.host}:{worker.port})")
        from antcode_core.application.services.workers.worker_heartbeat_service import (
            worker_heartbeat_service,
        )
        await worker_heartbeat_service.refresh_worker_cache(force=True)
        return worker, api_key, secret_key

    async def register_direct_worker(self, request) -> tuple[Worker, bool]:
        """Direct Worker 注册（使用 worker_id 作为 public_id）"""
        from antcode_core.common.security.worker_auth import worker_auth_verifier

        worker_id = (request.worker_id or "").strip()
        if not worker_id:
            raise ValueError("worker_id 不能为空")

        host, port = self.normalize_address(request.host, request.port)
        worker = await Worker.filter(public_id=worker_id).first()
        created = False

        if worker:
            if request.name and request.name != worker.name:
                exists = await Worker.filter(name=request.name).exclude(id=worker.id).first()
                if not exists:
                    worker.name = request.name
            if host:
                worker.host = host
            if port:
                worker.port = port
            if request.region is not None:
                worker.region = request.region
            if request.version:
                worker.version = request.version
            if request.os_type:
                worker.os_type = request.os_type
            if request.os_version:
                worker.os_version = request.os_version
            if request.python_version:
                worker.python_version = request.python_version
            if request.machine_arch:
                worker.machine_arch = request.machine_arch
            if request.capabilities:
                try:
                    worker.capabilities = request.capabilities.model_dump()
                except Exception:
                    worker.capabilities = request.capabilities
            worker.status = WorkerStatus.ONLINE.value
            worker.last_heartbeat = datetime.now(UTC)
            await worker.save()
            return worker, created

        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        base_name = ((request.name or "").strip() or worker_id)[:100]
        name = base_name
        if await Worker.filter(name=name).exists():
            candidate_names = [
                f"{base_name}-{worker_id[:6]}",
                f"{base_name}-{worker_id[-6:]}",
                f"worker-{worker_id[:12]}",
            ]

            selected_name = None
            for candidate in candidate_names:
                candidate = candidate[:100]
                if not await Worker.filter(name=candidate).exists():
                    selected_name = candidate
                    break

            if selected_name is None:
                base = base_name[:92]
                for _ in range(6):
                    candidate = f"{base}-{secrets.token_hex(3)}"
                    if not await Worker.filter(name=candidate).exists():
                        selected_name = candidate
                        break

            if selected_name is None:
                raise ValueError("无法为 Worker 生成唯一名称")

            name = selected_name

        worker = await Worker.create(
            public_id=worker_id,
            name=name,
            host=host or "",
            port=port or 0,
            region=request.region or "",
            version=request.version or None,
            status=WorkerStatus.ONLINE.value,
            api_key=api_key,
            secret_key=secret_key,
            last_heartbeat=datetime.now(UTC),
            os_type=request.os_type or None,
            os_version=request.os_version or None,
            python_version=request.python_version or None,
            machine_arch=request.machine_arch or None,
            capabilities=request.capabilities.model_dump() if request.capabilities else {},
            transport_mode="direct",
        )

        worker_auth_verifier.register_worker_secret(worker.public_id, secret_key)
        created = True
        logger.info(f"Direct Worker 注册成功: {worker.name} ({worker.public_id})")
        return worker, created

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
        """刷新 Worker 状态（基于心跳时间戳）"""
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
        """初始化时加载所有 Worker 密钥到验证器"""
        from antcode_core.common.security.worker_auth import worker_auth_verifier

        workers = await Worker.filter(secret_key__isnull=False).all()
        for worker in workers:
            if worker.secret_key:
                worker_auth_verifier.register_worker_secret(worker.public_id, worker.secret_key)

        logger.info(f"已加载 {len(workers)} 个 Worker 密钥")

    async def get_worker_credentials(self, worker: Worker) -> dict:
        """获取 Worker 凭证信息（用于配置 Worker）"""
        from antcode_core.common.config import settings

        return {
            "worker_id": worker.public_id,
            "api_key": worker.api_key,
            "secret_key": worker.secret_key,
            "gateway_host": settings.GATEWAY_HOST,
            "gateway_port": settings.GATEWAY_PORT,
            "transport_mode": settings.WORKER_TRANSPORT_MODE,
            "redis_url": settings.REDIS_URL,
        }


# 创建服务实例
worker_connection_service = WorkerConnectionService()
