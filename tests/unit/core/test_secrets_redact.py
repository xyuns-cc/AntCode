"""R2: secrets redact 三道闸的覆盖。"""

from __future__ import annotations

from antcode_core.common.logging import (
    DEFAULT_SENSITIVE_KEY_TOKENS,
    sanitize_dict,
    sanitize_log_message,
)


def test_default_sensitive_keys_cover_project_specific_terms():
    must_have = {
        "gateway_token",
        "worker_install_key",
        "encryption_key",
        "jwt_secret",
        "bearer",
        "redis_password",
        "client_key",
        "cookie",
        "session",
    }
    assert must_have.issubset(DEFAULT_SENSITIVE_KEY_TOKENS)


def test_sanitize_dict_masks_nested_secrets():
    raw = {
        "GATEWAY_TOKEN": "abc123",
        "level": 1,
        "nested": {"api_key": "supersecretvalue", "ok": "fine"},
    }
    masked = sanitize_dict(raw)
    assert masked["GATEWAY_TOKEN"] == "***REDACTED***"
    assert masked["nested"]["api_key"] == "***REDACTED***"
    assert masked["nested"]["ok"] == "fine"
    assert masked["level"] == 1


def test_sanitize_log_message_strips_db_url_passwords():
    msg = "connecting postgres://user:s3cret@db.example.com:5432/main"
    out = sanitize_log_message(msg)
    assert "s3cret" not in out
    assert "***" in out


def test_task_message_repr_redacts_environment_and_receipt():
    from antcode_worker.transport.base import TaskMessage

    msg = TaskMessage(
        task_id="t-1",
        project_id="p-1",
        environment={"DB_PASSWORD": "topsecret", "FOO": "bar"},
        receipt="raw-receipt-token",
    )
    text = repr(msg)
    assert "topsecret" not in text
    assert "***REDACTED***" in text
    # FOO 不是敏感键，但 bar 不会被作为完整 secret 触发；仍然不会泄露 password
    assert "raw-receipt-token" not in text
    assert "receipt='***'" not in text  # we used unquoted format
    assert "receipt=***" in text


def test_secrets_manager_masked_view_never_returns_plaintext():
    from antcode_worker.security.secrets import Credential, SecretsManager

    mgr = SecretsManager(secrets_dir=None)
    mgr._cache = {
        "api_key": Credential(key="api_key", value="abcdef123456", source="env"),
        "gateway_token": Credential(key="gateway_token", value="tokvaluexyz", source="file"),
    }
    view = mgr.masked_view()
    assert "abcdef123456" not in str(view)
    assert "tokvaluexyz" not in str(view)
    assert "len=12" in view["api_key"]
    assert "src=env" in view["api_key"]
    assert "src=file" in view["gateway_token"]
