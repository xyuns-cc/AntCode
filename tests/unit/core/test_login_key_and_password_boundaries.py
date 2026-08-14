import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from antcode_core.application.services.users.user_service import validate_password_strength
from antcode_core.domain.models.user import pwd_context


def _load_key_id(env: dict[str, str]) -> str:
    command = (
        "from antcode_core.common.security.login_crypto import LoginPasswordCrypto; "
        "print(LoginPasswordCrypto().public_key_payload()['key_id'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    return result.stdout.strip()


def test_concurrent_processes_share_one_login_key(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["LOGIN_RSA_PRIVATE_KEY_FILE"] = str(tmp_path / "login-private.pem")
    env["LOGIN_RSA_PUBLIC_KEY_FILE"] = str(tmp_path / "login-public.pem")

    with ThreadPoolExecutor(max_workers=8) as executor:
        key_ids = set(executor.map(_load_key_id, [env] * 8))

    assert len(key_ids) == 1


def test_bcrypt_rejects_distinct_passwords_with_same_72_byte_prefix() -> None:
    password = "A" * 68 + "a1!x"
    too_long = password + "different9!"
    password_hash = pwd_context.hash(password)

    assert pwd_context.verify(password, password_hash)
    assert not pwd_context.verify(too_long, password_hash)
    with pytest.raises(ValueError, match="72 字节"):
        pwd_context.hash(too_long)


def test_password_strength_counts_utf8_bytes() -> None:
    valid, error = validate_password_strength("测" * 23 + "Aa1!")

    assert valid is False
    assert "72 字节" in error
