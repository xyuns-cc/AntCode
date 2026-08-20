"""web_api 单元测试共享 fixture。"""

import base64
from collections.abc import Callable

import pytest
import pytest_asyncio
from antcode_core.common.config import settings
from antcode_core.common.security import login_crypto
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import Request
from tortoise import Tortoise


@pytest.fixture(scope="module")
def login_key(tmp_path_factory):
    """独立密钥，避免污染仓库 data_dir 下的真实登录私钥。"""
    key_dir = tmp_path_factory.mktemp("login-keys")
    crypto = login_crypto.LoginPasswordCrypto()
    crypto._resolve_private_key_path = lambda: key_dir / "private.pem"  # noqa: SLF001
    crypto._resolve_public_key_path = lambda: key_dir / "public.pem"  # noqa: SLF001
    return crypto


@pytest.fixture
def crypto(monkeypatch, login_key):
    """默认策略（强制密文）下的登录密钥；四条口令路由共用它。"""
    monkeypatch.setattr(login_crypto, "login_password_crypto", login_key)
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOGIN_PASSWORD_ENCRYPTION_REQUIRED", True)
    return login_key


@pytest.fixture
def encrypt_password(crypto) -> Callable[[str], str]:
    """用当前登录公钥加密一个口令，模拟浏览器提交的密文。"""

    def _encrypt(plaintext: str) -> str:
        public_key = crypto._get_private_key().public_key()  # noqa: SLF001
        cipher = public_key.encrypt(
            plaintext.encode("utf-8"),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return base64.b64encode(cipher).decode("ascii")

    return _encrypt


@pytest.fixture
def http_request() -> Request:
    """带 client 地址的最小 ASGI 请求；审计写入要从它取 IP。"""
    return Request({"type": "http", "client": ("127.0.0.1", 1234)})


@pytest_asyncio.fixture
async def audit_table():
    """真表 audit_logs。审计用例断言的是"库里真的多出一行"，所以不能 mock 掉写入。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()
