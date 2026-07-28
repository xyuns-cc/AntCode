"""Bounded outbound probe for rule-project connection tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from antcode_core.application.services.projects.git_process_limits import resolve_with_timeout
from antcode_core.application.services.projects.git_url_security import resolve_webhook_url


class ProjectConnectionError(RuntimeError):
    """Base error for an outbound project connection probe."""


class ProjectConnectionTimeoutError(ProjectConnectionError):
    """The absolute probe deadline expired."""


class ProjectConnectionResponseTooLargeError(ProjectConnectionError):
    """The upstream response exceeded the configured byte limit."""


@dataclass(frozen=True)
class ProjectConnectionResult:
    status_code: int


async def probe_project_connection(
    url: str,
    method: str,
    *,
    headers: dict[str, str] | None,
    cookies: dict[str, str] | None,
    timeout_seconds: float,
    max_response_bytes: int,
) -> ProjectConnectionResult:
    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with asyncio.timeout(timeout_seconds):
            endpoint = await asyncio.to_thread(resolve_with_timeout, resolve_webhook_url, url, timeout_seconds)
            request_headers = dict(headers or {})
            request_headers["Host"] = endpoint.host_header()
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream(
                    method.upper(),
                    endpoint.pinned_http_url(),
                    headers=request_headers,
                    cookies=cookies,
                    extensions={"sni_hostname": endpoint.host},
                ) as response:
                    await _read_bounded_response(response, max_response_bytes)
                    return ProjectConnectionResult(status_code=response.status_code)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ProjectConnectionTimeoutError(f"连接测试超过绝对超时 {timeout_seconds:g} 秒") from exc
    except ProjectConnectionError:
        raise
    except RuntimeError as exc:
        raise ProjectConnectionError("连接测试 DNS 解析资源不可用") from exc
    except httpx.HTTPError as exc:
        raise ProjectConnectionError("连接测试请求失败") from exc


async def _read_bounded_response(response: httpx.Response, max_response_bytes: int) -> None:
    if response.request.method == "HEAD" or response.status_code in {204, 304} or 100 <= response.status_code < 200:
        return
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise ProjectConnectionError("上游响应 Content-Length 无效") from exc
        if declared_size > max_response_bytes:
            raise ProjectConnectionResponseTooLargeError(f"连接测试响应体超过上限 {max_response_bytes} 字节")
    received = 0
    async for chunk in response.aiter_raw():
        received += len(chunk)
        if received > max_response_bytes:
            raise ProjectConnectionResponseTooLargeError(f"连接测试响应体超过上限 {max_response_bytes} 字节")


__all__ = [
    "ProjectConnectionError",
    "ProjectConnectionResponseTooLargeError",
    "ProjectConnectionResult",
    "ProjectConnectionTimeoutError",
    "probe_project_connection",
]
