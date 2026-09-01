import pytest
from antcode_core.common.config import settings
from antcode_core.common.security.secret_box import SecretBox
from cryptography.fernet import Fernet, InvalidToken


def _pbkdf2_ciphertext(key_material: str, salt: str, plaintext: str) -> str:
    key = SecretBox()._derive_fernet_key(key_material, salt)
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def test_password_material_requires_explicit_unique_salt(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "high-entropy-test-material-at-least-32-bytes")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY_SALT"):
        SecretBox().encrypt("secret")


def test_legacy_fixed_salt_requires_explicit_migration_setting(monkeypatch):
    key_material = "high-entropy-test-material-at-least-32-bytes"
    ciphertext = _pbkdf2_ciphertext(key_material, "antcode-fernet-v1", "secret")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key_material)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "unique-deployment-salt")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")

    with pytest.raises(InvalidToken):
        SecretBox().decrypt(ciphertext)

    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "antcode-fernet-v1")
    assert SecretBox().decrypt(ciphertext) == "secret"


def test_primary_only_verification_detects_and_rotates_legacy_key(monkeypatch):
    primary = Fernet.generate_key().decode("ascii")
    legacy = Fernet.generate_key().decode("ascii")
    ciphertext = Fernet(legacy.encode("ascii")).encrypt(b"secret").decode("ascii")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy)
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    box = SecretBox()

    assert box.needs_rotation(ciphertext) is True
    with pytest.raises(InvalidToken):
        box.decrypt_primary(ciphertext)

    rotated = box.rotate(ciphertext)
    assert box.decrypt_primary(rotated) == "secret"
    assert box.needs_rotation(rotated) is False
