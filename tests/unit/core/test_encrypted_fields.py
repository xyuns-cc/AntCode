import json

from antcode_core.common import config
from antcode_core.common.security.encrypted_fields import EncryptedJSONField, EncryptedTextField
from antcode_core.common.security.secret_box import secret_box


def _set_key(monkeypatch):
    monkeypatch.setattr(config.settings, "ENCRYPTION_KEY", "test-encrypted-field-key-32-bytes-min")
    monkeypatch.setattr(config.settings, "ENCRYPTION_KEY_SALT", "encrypted-field-test-salt")
    monkeypatch.setattr(config.settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(config.settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)
    secret_box._cached = None
    secret_box._cache_key = None


def test_encrypted_text_field_keeps_plaintext_out_of_database_value(monkeypatch):
    _set_key(monkeypatch)
    field = EncryptedTextField()

    stored = field.to_db_value("smtp-password", object)

    assert stored is not None and "smtp-password" not in stored
    assert field.to_python_value(stored) == "smtp-password"


def test_encrypted_json_field_round_trips_and_reads_legacy_plaintext(monkeypatch):
    _set_key(monkeypatch)
    field = EncryptedJSONField()
    payload = {"Authorization": "Bearer secret", "nested": {"password": "value"}}

    stored = field.to_db_value(payload, object)

    assert stored is not None and "Bearer secret" not in stored
    assert field.to_python_value(stored) == payload
    assert field.to_python_value(json.dumps(payload)) == payload


def test_forged_text_prefix_is_not_trusted_and_round_trips(monkeypatch):
    _set_key(monkeypatch)
    field = EncryptedTextField()

    # 用户提交一个伪造 "已加密" 前缀的明文 —— 直通落库会让读取时
    # decrypt 抛 InvalidToken, 整行永久不可读(存储型 DoS)。
    stored = field.to_db_value("enc:v1:not-a-real-ciphertext", object)

    assert stored is not None
    assert field.to_python_value(stored) == "enc:v1:not-a-real-ciphertext"


def test_forged_json_marker_is_not_trusted_and_round_trips(monkeypatch):
    _set_key(monkeypatch)
    field = EncryptedJSONField()

    forged = {"__antcode_encrypted_v1__": "garbage"}
    stored = field.to_db_value(forged, object)

    assert stored is not None
    assert field.to_python_value(stored) == forged
