import pytest
from antcode_core.common.config import settings
from antcode_core.common.security.secret_box import SecretBox
from cryptography.fernet import Fernet, InvalidToken


def test_password_material_requires_explicit_unique_salt(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "high-entropy-test-material-at-least-32-bytes")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")

    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY_SALT"):
        SecretBox().encrypt("secret")


def test_primary_only_verification_detects_and_rotates_legacy_key(monkeypatch):
    primary = Fernet.generate_key().decode("ascii")
    legacy = Fernet.generate_key().decode("ascii")
    ciphertext = Fernet(legacy.encode("ascii")).encrypt(b"secret").decode("ascii")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", primary)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", legacy)
    box = SecretBox()

    assert box.needs_rotation(ciphertext) is True
    with pytest.raises(InvalidToken):
        box.decrypt_primary(ciphertext)

    rotated = box.rotate(ciphertext)
    assert box.decrypt_primary(rotated) == "secret"
    assert box.needs_rotation(rotated) is False
