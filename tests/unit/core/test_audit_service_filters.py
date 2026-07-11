import importlib
from datetime import datetime
from types import SimpleNamespace

import pytest
from antcode_core.application.services.audit.audit_service import AuditService


class _DummyQuery:
    def __init__(self, log):
        self.log = log
        self.filters = []

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    async def count(self):
        return 1

    def order_by(self, *_fields):
        return self

    def offset(self, _value):
        return self

    async def limit(self, _value):
        return [self.log]


@pytest.mark.asyncio
async def test_get_logs_applies_user_id_and_returns_user_id(monkeypatch):
    service = AuditService()
    audit_module = importlib.import_module("antcode_core.application.services.audit.audit_service")

    log = SimpleNamespace(
        action=SimpleNamespace(value="user_update"),
        resource_type="user",
        resource_id="u-1",
        resource_name="admin",
        user_id=123,
        username="operator",
        ip_address="127.0.0.1",
        description="更新用户",
        old_value={"username": "old_admin"},
        new_value={"username": "new_admin"},
        success=True,
        error_message=None,
        created_at=datetime.now(),
    )

    query = _DummyQuery(log)

    class _FakeAuditLog:
        @staticmethod
        def all():
            return query

    monkeypatch.setattr(audit_module, "AuditLog", _FakeAuditLog)

    result = await service.get_logs(user_id=123)

    assert {"user_id": 123} in query.filters
    assert result["items"][0]["user_id"] == 123
