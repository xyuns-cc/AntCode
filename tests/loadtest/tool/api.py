from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import LoadSettings
from .runner import OperationResult

API_PREFIX = "/api/v1"
TASK_DELETE_BATCH_SIZE = 100


class AntCodeApi:
    def __init__(self, settings: LoadSettings) -> None:
        limits = httpx.Limits(
            max_connections=settings.stage.vus,
            max_keepalive_connections=settings.stage.vus,
        )
        self._tokens = settings.tokens
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(settings.timeout_seconds),
            limits=limits,
        )

    async def __aenter__(self) -> AntCodeApi:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._client.aclose()

    def headers(self, index: int) -> dict[str, str]:
        token = self._tokens[index % len(self._tokens)]
        return {"Authorization": f"Bearer {token}"}

    def stream(self, method: str, path: str, index: int = 0):
        """流式请求（SSE 日志流等长响应）。"""
        return self._client.stream(method, f"{API_PREFIX}{path}", headers=self.headers(index))

    async def call(
        self,
        method: str,
        path: str,
        index: int,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> OperationResult[Any]:
        response = await self._client.request(
            method,
            f"{API_PREFIX}{path}",
            headers=self.headers(index),
            json=json_body,
        )
        value = _decode_response(response)
        return OperationResult(response.status_code, value, _business_error(value))

    async def require_json(
        self,
        method: str,
        path: str,
        index: int = 0,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            method,
            f"{API_PREFIX}{path}",
            headers=self.headers(index),
            json=json_body,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"{path} returned a non-object JSON response")
        if payload.get("success") is False:
            raise RuntimeError(f"{path} returned success=false")
        return payload

    async def create_task(self, body: dict[str, Any], index: int = 0) -> str:
        payload = await self.require_json("POST", "/tasks", index, json_body=body)
        return _public_id(payload, "/tasks")

    async def delete_tasks(self, task_ids: list[str]) -> None:
        for offset in range(0, len(task_ids), TASK_DELETE_BATCH_SIZE):
            await self._delete_task_batch(task_ids[offset : offset + TASK_DELETE_BATCH_SIZE])
        await self._verify_tasks_deleted(task_ids)

    async def _delete_task_batch(self, task_ids: list[str]) -> None:
        payload = await self.require_json("POST", "/tasks/batch-delete", json_body={"task_ids": task_ids})
        data = _data(payload, "/tasks/batch-delete")
        cleanup_complete = (
            data.get("success_count") == len(task_ids)
            and data.get("failed_count") == 0
            and data.get("failed_ids") == []
        )
        if not cleanup_complete:
            raise RuntimeError(f"load-test cleanup failed for: {data.get('failed_ids')}")

    async def _verify_tasks_deleted(self, task_ids: list[str]) -> None:
        for offset in range(0, len(task_ids), TASK_DELETE_BATCH_SIZE):
            batch = task_ids[offset : offset + TASK_DELETE_BATCH_SIZE]
            statuses = await asyncio.gather(*(self._task_status(task_id) for task_id in batch))
            remaining = [task_id for task_id, status in zip(batch, statuses, strict=True) if status != 404]
            if remaining:
                raise RuntimeError(f"load-test cleanup left tasks accessible: {remaining}")

    async def _task_status(self, task_id: str) -> int:
        response = await self._client.get(f"{API_PREFIX}/tasks/{task_id}", headers=self.headers(0))
        return response.status_code

    async def task_runs(self, task_id: str, index: int = 0) -> list[dict[str, Any]]:
        payload = await self.require_json("GET", f"/tasks/{task_id}/runs?page=1&size=1", index)
        data = _data(payload, "/tasks/{task_id}/runs")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("task runs response has no items list")
        return [item for item in items if isinstance(item, dict)]


def _decode_response(response: httpx.Response) -> Any:
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    return response.text


def _business_error(value: Any) -> str | None:
    if isinstance(value, dict) and value.get("success") is False:
        return "api_success_false"
    return None


def _data(payload: dict[str, Any], path: str) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{path} response has no data object")
    return data


def _public_id(payload: dict[str, Any], path: str) -> str:
    public_id = _data(payload, path).get("id")
    if not isinstance(public_id, str) or not public_id:
        raise ValueError(f"{path} response has no public id")
    return public_id
