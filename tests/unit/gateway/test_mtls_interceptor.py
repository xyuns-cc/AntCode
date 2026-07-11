"""Gateway mTLS 证书身份与已认证 Worker 主体绑定测试。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


def _load_auth_module():
    """绕开 gateway/__init__.py 的全局副作用，单独导入 auth.py。"""
    if "antcode_gateway.auth" in sys.modules:
        return sys.modules["antcode_gateway.auth"]
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "services/gateway/src/antcode_gateway/auth.py"
    spec = importlib.util.spec_from_file_location("antcode_gateway.auth", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["antcode_gateway.auth"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def auth_mod():
    return _load_auth_module()


def _context(auth_context):
    context = MagicMock()
    context.auth_context.return_value = auth_context
    return context


def test_mtls_accepts_matching_cn(auth_mod):
    ok, reason = auth_mod.AuthInterceptor._check_mtls_binding(
        _context({"x509_common_name": [b"worker-7"]}),
        "worker-7",
    )
    assert ok is True
    assert reason == ""


def test_mtls_accepts_matching_san(auth_mod):
    auth_ctx = {
        "x509_common_name": [b"someone-else"],
        "x509_subject_alternative_name": [b"worker-7"],
    }
    ok, _ = auth_mod.AuthInterceptor._check_mtls_binding(_context(auth_ctx), "worker-7")
    assert ok is True


def test_mtls_rejects_mismatch(auth_mod):
    ok, reason = auth_mod.AuthInterceptor._check_mtls_binding(
        _context({"x509_common_name": [b"some-other-worker"]}),
        "worker-7",
    )
    assert ok is False
    assert "不匹配" in reason


def test_mtls_allows_missing_cert_in_non_mtls_environment(auth_mod):
    ok, reason = auth_mod.AuthInterceptor._check_mtls_binding(_context({}), "worker-7")
    assert ok is True
    assert reason == ""


def test_mtls_accepts_when_no_worker_id_required(auth_mod):
    ok, reason = auth_mod.AuthInterceptor._check_mtls_binding(
        _context({"x509_common_name": [b"anything"]}),
        "",
    )
    assert ok is True
    assert reason == ""


def test_mtls_rejects_unreadable_auth_context(auth_mod):
    context = MagicMock()
    context.auth_context.side_effect = RuntimeError("unavailable")

    ok, reason = auth_mod.AuthInterceptor._check_mtls_binding(context, "worker-7")

    assert ok is False
    assert "auth_context" in reason
