import base64
import hashlib

import pytest
from antcode_core.common.config import settings
from antcode_core.common.security.secret_box import SecretBox
from cryptography.fernet import Fernet, InvalidToken


def _legacy_ciphertext(key_material: str, plaintext: str) -> str:
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _pbkdf2_ciphertext(key_material: str, salt: str, plaintext: str) -> str:
    key = SecretBox()._derive_fernet_key(key_material, salt)
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def test_password_material_requires_explicit_unique_salt(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "high-entropy-test-material-at-least-32-bytes")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY_SALT"):
        SecretBox().encrypt("secret")


def test_legacy_sha256_decrypt_requires_explicit_migration_switch(monkeypatch):
    key_material = "high-entropy-test-material-at-least-32-bytes"
    ciphertext = _legacy_ciphertext(key_material, "secret")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key_material)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "unique-deployment-salt")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)

    with pytest.raises(InvalidToken):
        SecretBox().decrypt(ciphertext)

    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", True)
    assert SecretBox().decrypt(ciphertext) == "secret"


def test_legacy_fixed_salt_requires_explicit_migration_setting(monkeypatch):
    key_material = "high-entropy-test-material-at-least-32-bytes"
    ciphertext = _pbkdf2_ciphertext(key_material, "antcode-fernet-v1", "secret")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key_material)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "unique-deployment-salt")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)

    with pytest.raises(InvalidToken):
        SecretBox().decrypt(ciphertext)

    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "antcode-fernet-v1")
    assert SecretBox().decrypt(ciphertext) == "secret"
