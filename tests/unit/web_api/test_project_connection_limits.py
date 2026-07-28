from types import SimpleNamespace

import httpx
import pytest
from antcode_web_api.services import project_connection as module


def _endpoint():
    return SimpleNamespace(
        host_header=lambda: "example.com",
        pinned_http_url=lambda: "https://93.184.216.34/probe",
        host="example.com",
    )


@pytest.mark.asyncio
async def test_probe_streams_and_rejects_oversized_response(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 11))
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(module, "resolve_webhook_url", lambda url: _endpoint())
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)

    with pytest.raises(module.ProjectConnectionResponseTooLargeError, match="超过上限"):
        await module.probe_project_connection(
            "https://example.com/probe",
            "GET",
            headers=None,
            cookies=None,
            timeout_seconds=1,
            max_response_bytes=10,
        )


@pytest.mark.asyncio
async def test_probe_absolute_timeout_includes_dns_resolution(monkeypatch):
    async def blocked_to_thread(*args, **kwargs):
        del args, kwargs
        await __import__("asyncio").sleep(1)

    monkeypatch.setattr(module.asyncio, "to_thread", blocked_to_thread)

    with pytest.raises(module.ProjectConnectionTimeoutError, match="绝对超时"):
        await module.probe_project_connection(
            "https://example.com/probe",
            "GET",
            headers=None,
            cookies=None,
            timeout_seconds=0.01,
            max_response_bytes=10,
        )


@pytest.mark.asyncio
async def test_probe_returns_status_after_consuming_bounded_body(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(204, content=b""))
    client = httpx.AsyncClient(transport=transport)
    monkeypatch.setattr(module, "resolve_webhook_url", lambda url: _endpoint())
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client)

    result = await module.probe_project_connection(
        "https://example.com/probe",
        "HEAD",
        headers={"X-Test": "1"},
        cookies=None,
        timeout_seconds=1,
        max_response_bytes=10,
    )

    assert result.status_code == 204
