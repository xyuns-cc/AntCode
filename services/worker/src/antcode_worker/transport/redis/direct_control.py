"""Authenticated HTTP control-plane client for Direct Workers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
from antcode_core.common.utils.worker_request import (
    HTTP_POST_METHOD,
    build_worker_signed_headers,
    encode_worker_json_body,
    request_path_from_url,
)

from antcode_worker.transport.base import GenerationLostError

CONTROL_REQUEST_TIMEOUT_SECONDS = 15.0
HTTP_ERROR_STATUS = 400


@dataclass(frozen=True)
class DirectLeaseGrant:
    """Direct 控制面一次 Lease 签发/续租的完整应答。

    ``ttl_ms`` 与 Gateway 的 ``LeaseResponse.ttl_ms`` 同源同义：服务端权威
    TTL，Worker 用它校验 ``renew_after_ms * 2 < ttl_ms``。
    """

    lease_id: str
    expires_at_ms: int
    renew_after_ms: int
    ttl_ms: int
    revoked: bool


class DirectControlClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        worker_id: str,
        api_key: str,
        secret_key: str,
        allow_insecure_internal: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_base_url or not worker_id or not api_key or not secret_key:
            raise ValueError("Direct control client 配置不完整")
        from antcode_worker.app.worker_registration import normalize_api_base_url

        self._base_url = normalize_api_base_url(
            api_base_url,
            "",
            allow_insecure_internal=allow_insecure_internal,
        )
        self._worker_id = worker_id
        self._api_key = api_key
        self._secret_key = secret_key
        self._client = client
        self._owns_client = client is None

    @staticmethod
    def _new_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=CONTROL_REQUEST_TIMEOUT_SECONDS)

    async def lease_renew(
        self,
        current_lease_id: str,
        metrics: dict | None,
        capabilities: dict[str, str],
    ) -> DirectLeaseGrant:
        payload = {
            "operation": "lease",
            "current_lease_id": current_lease_id,
            "metrics": metrics,
            "capabilities": capabilities,
        }
        data = await self._post("lease", payload)
        return DirectLeaseGrant(
            lease_id=self._required_string(data, "lease_id", allow_empty=bool(data.get("revoked"))),
            expires_at_ms=self._required_int(data, "expires_at_ms"),
            renew_after_ms=self._required_int(data, "renew_after_ms"),
            # 缺 ttl_ms 直接判协议不合法：Direct 控制面与本 Worker 同版本发布，
            # 缺字段只可能是签发路径漏填，静默放过等于让双执行守卫失效。
            ttl_ms=self._required_int(data, "ttl_ms"),
            revoked=self._required_bool(data, "revoked"),
        )

    async def claim_run_ownership(self, lease_id: str, run_id: str, ttl_ms: int) -> bool:
        data = await self._ownership_call("claim", lease_id, run_id, ttl_ms=ttl_ms)
        return self._required_bool(data, "acquired")

    async def renew_run_ownership(self, lease_id: str, run_id: str, ttl_ms: int) -> bool:
        data = await self._ownership_call("renew", lease_id, run_id, ttl_ms=ttl_ms)
        return self._required_bool(data, "renewed")

    async def release_run_ownership(self, lease_id: str, run_id: str) -> bool:
        data = await self._ownership_call("release", lease_id, run_id)
        return self._required_bool(data, "released")

    async def report_spider_items(
        self,
        *,
        lease_id: str,
        run_id: str,
        project_id: str,
        items: list[dict[str, Any]],
    ) -> bool:
        data = await self._post(
            "spider-items",
            {
                "operation": "spider-items",
                "lease_id": lease_id,
                "run_id": run_id,
                "project_id": project_id,
                "items": items,
            },
        )
        return self._required_bool(data, "written")

    async def update_spider_meta(
        self,
        *,
        lease_id: str,
        run_id: str,
        project_id: str,
        meta: dict[str, Any],
    ) -> bool:
        data = await self._post(
            "spider-meta",
            {
                "operation": "spider-meta",
                "lease_id": lease_id,
                "run_id": run_id,
                "project_id": project_id,
                "meta": meta,
            },
        )
        return self._required_bool(data, "written")

    async def report_log_batch(self, payload: bytes) -> bool:
        if not payload:
            raise ValueError("Direct 日志批次不能为空")
        data = await self._post(
            "logs",
            {
                "operation": "logs",
                "payload_base64": base64.b64encode(payload).decode("ascii"),
            },
        )
        return self._required_bool(data, "written")

    async def deregister(self, lease_id: str, reason: str) -> bool:
        data = await self._post(
            "deregister",
            {"operation": "deregister", "lease_id": lease_id, "reason": reason},
        )
        return self._required_bool(data, "revoked")

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _ownership_call(
        self,
        action: str,
        lease_id: str,
        run_id: str,
        *,
        ttl_ms: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"operation": action, "lease_id": lease_id, "run_id": run_id}
        if ttl_ms is not None:
            payload["ttl_ms"] = ttl_ms
        return await self._post(f"run-ownership/{action}", payload)

    async def _post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None or (self._owns_client and self._client.is_closed):
            self._client = self._new_client()
        url = f"{self._base_url}/api/v1/workers/{self._worker_id}/direct-control/{operation}"
        body = encode_worker_json_body(payload)
        worker = SimpleNamespace(public_id=self._worker_id)
        headers = build_worker_signed_headers(
            worker,
            api_key=self._api_key,
            secret_key=self._secret_key,
            method=HTTP_POST_METHOD,
            path=request_path_from_url(url),
            body=body,
        )
        response = await self._client.post(url, content=body, headers=headers)
        return self._response_data(response, operation)

    @staticmethod
    def _response_data(response: httpx.Response, operation: str) -> dict[str, Any]:
        if response.status_code == httpx.codes.PRECONDITION_FAILED:
            raise GenerationLostError(f"Direct control {operation} 的 Lease generation 已失效")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Direct control {operation} 返回非 JSON 响应") from exc
        if response.status_code >= HTTP_ERROR_STATUS:
            detail = body.get("detail") if isinstance(body, dict) else None
            raise RuntimeError(detail or f"Direct control {operation} 失败 (HTTP {response.status_code})")
        if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), dict):
            raise RuntimeError(f"Direct control {operation} 返回协议不合法")
        return body["data"]

    @staticmethod
    def _required_bool(data: dict[str, Any], field: str) -> bool:
        value = data.get(field)
        if not isinstance(value, bool):
            raise RuntimeError(f"Direct control 响应缺少布尔字段: {field}")
        return value

    @staticmethod
    def _required_int(data: dict[str, Any], field: str) -> int:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeError(f"Direct control 响应缺少整数字段: {field}")
        return value

    @staticmethod
    def _required_string(data: dict[str, Any], field: str, *, allow_empty: bool) -> str:
        value = data.get(field)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise RuntimeError(f"Direct control 响应缺少字符串字段: {field}")
        return value


__all__ = ["DirectControlClient", "DirectLeaseGrant"]
