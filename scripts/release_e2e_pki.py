"""Generate the short-lived PKI used by the production release E2E gate."""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PRIVATE_FILE_MODE = 0o600
CONTAINER_FILE_MODE = 0o444
CERTIFICATE_LIFETIME_DAYS = 2
RSA_KEY_SIZE = 2048
MAX_COMMON_NAME_LENGTH = 64
#: 每个 Worker 必须有独立的 mTLS 目录：客户端证书的 CN 就是它自己的 worker_id，
#: 多个 Worker 共用一个目录等于共用一张身份证书，CN 绑定会立刻把后来的全部拒掉。
DEFAULT_WORKER_TLS_DIR = "worker-tls"


def _write(path: Path, value: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(mode)
    temporary.replace(path)


def _private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)


def _certificate_builder(common_name: str) -> x509.CertificateBuilder:
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERTIFICATE_LIFETIME_DAYS))
    )


def _create_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _private_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AntCode release E2E CA")])
    cert = (
        _certificate_builder("AntCode release E2E CA")
        .issuer_name(subject)
        .public_key(key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _create_leaf(
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    *,
    client: bool,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _private_key()
    usage = ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH
    builder = (
        _certificate_builder(common_name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    )
    if not client:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("host.docker.internal"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
    return key, builder.sign(ca_key, hashes.SHA256())


def _pem_key(key: rsa.RSAPrivateKey) -> str:
    value = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return value.decode("ascii")


def _pem_cert(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _write_leaf(
    root: Path,
    directory: str,
    names: tuple[str, str],
    *,
    common_name: str,
    client: bool,
) -> None:
    ca_key = serialization.load_pem_private_key((root / "ca.key").read_bytes(), password=None)
    if not isinstance(ca_key, rsa.RSAPrivateKey):
        raise TypeError("release E2E CA private key must be RSA")
    ca_cert = x509.load_pem_x509_certificate((root / "public-ca.crt").read_bytes())
    key, cert = _create_leaf(ca_key, ca_cert, common_name, client=client)
    _write(root / directory / names[0], _pem_key(key), CONTAINER_FILE_MODE)
    _write(root / directory / names[1], _pem_cert(cert), CONTAINER_FILE_MODE)


def write_release_pki(root: Path, *, worker_directories: Sequence[str] = (DEFAULT_WORKER_TLS_DIR,)) -> None:
    ca_key, ca_cert = _create_ca()
    _write(root / "ca.key", _pem_key(ca_key), PRIVATE_FILE_MODE)
    _write(root / "public-ca.crt", _pem_cert(ca_cert), CONTAINER_FILE_MODE)
    for directory in ("gateway-tls", *worker_directories):
        _write(root / directory / "ca.crt", _pem_cert(ca_cert), CONTAINER_FILE_MODE)
    _write_leaf(
        root,
        "gateway-tls",
        ("server.key", "server.crt"),
        common_name="gateway",
        client=False,
    )
    _write_leaf(
        root,
        "public-tls",
        ("tls.key", "tls.crt"),
        common_name="public-api",
        client=False,
    )
    for directory in worker_directories:
        write_worker_identity_certificate(root, "release-bootstrap", directory=directory)


def write_worker_identity_certificate(
    root: Path,
    worker_id: str,
    *,
    directory: str = DEFAULT_WORKER_TLS_DIR,
) -> None:
    identity = worker_id.strip()
    if not identity or len(identity) > MAX_COMMON_NAME_LENGTH or not identity.isprintable():
        raise ValueError("invalid Worker certificate identity")
    _write_leaf(
        root,
        directory,
        ("client.key", "client.crt"),
        common_name=identity,
        client=True,
    )
