from types import SimpleNamespace

import pytest
from antcode_web_api.routes.v1 import audit as audit_route


@pytest.mark.asyncio
async def test_get_audit_logs_passes_user_id_filter(monkeypatch):
    admin_user = SimpleNamespace(is_admin=True)

    captured_kwargs = {}

    async def fake_get_logs(**kwargs):
        captured_kwargs.update(kwargs)
        return {"total": 0, "page": 1, "page_size": 50, "items": []}

    monkeypatch.setattr(audit_route.audit_service, "get_logs", fake_get_logs)

    response = await audit_route.get_audit_logs(
        page=1,
        page_size=50,
        action=None,
        resource_type=None,
        username=None,
        user_id=42,
        start_date=None,
        end_date=None,
        success_filter=None,
        _admin=admin_user,
    )

    assert response.success is True
    assert captured_kwargs["user_id"] == 42
