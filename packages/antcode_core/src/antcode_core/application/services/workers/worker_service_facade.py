"""Operational delegates composed into the Worker CRUD service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from antcode_core.application.services.workers.worker_direct_registration import register_direct_worker
from antcode_core.common.security.api_key import verify_api_key_hash
from antcode_core.domain.models import Worker


class WorkerServiceFacade:
    _heartbeat_service: Any
    _connection_service: Any
    _stats_service: Any
    _lease_enabler: Any

    async def init_heartbeat_cache(self):
        await self._heartbeat_service.init_heartbeat_cache()

    async def refresh_worker_cache(self):
        await self._heartbeat_service.refresh_worker_cache()

    async def init_worker_secrets(self):
        await self._connection_service.init_worker_secrets()

    async def register_direct_worker(self, request):
        if self._lease_enabler is None:
            raise RuntimeError("Worker lease lifecycle enabler is not configured")
        return await register_direct_worker(request, service=self)

    async def smart_health_check(self):
        return await self._heartbeat_service.smart_health_check()

    async def check_all_workers_health(self):
        return await self._heartbeat_service.check_all_workers_health()

    async def manual_test_worker(self, worker_id: int) -> bool:
        return await self._heartbeat_service.manual_test_worker(worker_id)

    async def get_aggregate_stats(self):
        return await self._stats_service.get_aggregate_stats()

    async def get_metrics_history(self, worker_id: int, hours: int = 24):
        return await self._stats_service.get_metrics_history(worker_id, hours)

    async def get_cluster_metrics_history(self, hours: int = 24):
        return await self._stats_service.get_cluster_metrics_history(hours)

    async def get_all_workers(self):
        workers = await Worker.all().order_by("-created_at")
        await self._heartbeat_service.check_offline_workers(workers)
        return workers

    async def get_worker_by_id(self, worker_id) -> Worker | None:
        worker = await Worker.filter(public_id=str(worker_id)).first()
        if worker:
            return worker
        try:
            return await Worker.filter(id=int(worker_id)).first()
        except (ValueError, TypeError):
            return None

    async def get_worker_by_public_id(self, public_id: str) -> Worker | None:
        return await Worker.filter(public_id=public_id).first()

    async def register_worker(self, request):
        return await self._connection_service.register_worker(request)

    async def test_connection(self, worker_id):
        worker = await self.get_worker_by_id(worker_id)
        return (
            await self._connection_service.test_connection(worker)
            if worker
            else {
                "success": False,
                "error": "Worker 不存在",
            }
        )

    async def refresh_worker_status(self, worker_id):
        worker = await self.get_worker_by_id(worker_id)
        return await self._connection_service.refresh_worker_status(worker) if worker else None

    @staticmethod
    async def verify_api_key(worker: Worker, api_key: str) -> bool:
        if not worker or not api_key:
            return False
        if worker.api_key_hash and verify_api_key_hash(api_key, worker.api_key_hash):
            return True
        previous = worker.api_key_previous_hash
        if not previous or not verify_api_key_hash(api_key, previous):
            return False
        expires_at = worker.api_key_previous_expires_at
        if expires_at is None:
            return False
        expiry = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
        return expiry > datetime.now(UTC)


__all__ = ["WorkerServiceFacade"]
