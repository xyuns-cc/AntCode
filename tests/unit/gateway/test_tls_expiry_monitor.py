"""Gateway 必须在 TLS 材料到期**之前**出声。

热更新解决的是"换不了"：材料一变，新连接立刻用新材料。但材料不变就不会触发任何
回调——一张还有三天到期的证书躺在盘上，Gateway 一个字都不会说，直到某天全队
Worker 同时握手失败。这里钉死的是那条提前量，以及"读不出有效期"必须按失败上报
而不是当成没问题。

判据一律匹配结构化码常量，不匹配中文描述。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_gateway import main as gateway_main
from antcode_gateway.config import GatewayConfig
from antcode_gateway.server import GrpcServer
from antcode_gateway.tls_expiry import (
    SECONDS_PER_DAY,
    TLS_MATERIAL_EXPIRED,
    TLS_MATERIAL_EXPIRING,
    TLS_MATERIAL_EXPIRY_OK,
    TLS_MATERIAL_EXPIRY_UNREADABLE,
    TlsExpiryMonitor,
    TlsExpiryPolicy,
)
from antcode_gateway.tls_material import TlsMaterialLoader, TlsMaterialPaths
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from loguru import logger

from scripts.release_e2e_pki import RSA_KEY_SIZE, _pem_cert, _pem_key

RSA_PUBLIC_EXPONENT = 65537
WARNING_DAYS = 30
CHECK_INTERVAL_SECONDS = 3_600
LONG_LIVED_DAYS = 400
INSIDE_WINDOW_DAYS = 3
EXPIRED_HOURS = 1


@dataclass(frozen=True)
class _Issued:
    key: rsa.RSAPrivateKey
    certificate: x509.Certificate


def _policy() -> TlsExpiryPolicy:
    return TlsExpiryPolicy(
        warning_seconds=WARNING_DAYS * SECONDS_PER_DAY,
        interval_seconds=CHECK_INTERVAL_SECONDS,
    )


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _builder(subject: x509.Name, not_after: datetime) -> x509.CertificateBuilder:
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .serial_number(x509.random_serial_number())
        # 早于任何 not_after，过期证书才构造得出来。
        .not_valid_before(datetime.now(UTC) - timedelta(days=LONG_LIVED_DAYS))
        .not_valid_after(not_after)
    )


def _authority(not_after: datetime) -> _Issued:
    key = rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_SIZE)
    subject = _name("expiry-test-ca")
    certificate = (
        _builder(subject, not_after)
        .issuer_name(subject)
        .public_key(key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return _Issued(key=key, certificate=certificate)


def _leaf(authority: _Issued, not_after: datetime) -> _Issued:
    key = rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_SIZE)
    certificate = (
        _builder(_name("gateway"), not_after)
        .issuer_name(authority.certificate.subject)
        .public_key(key.public_key())
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(authority.key, hashes.SHA256())
    )
    return _Issued(key=key, certificate=certificate)


def _write(root: Path, server: _Issued, authority: _Issued) -> TlsMaterialPaths:
    root.mkdir(parents=True, exist_ok=True)
    (root / "server.crt").write_text(_pem_cert(server.certificate), encoding="utf-8")
    (root / "server.key").write_text(_pem_key(server.key), encoding="utf-8")
    (root / "ca.crt").write_text(_pem_cert(authority.certificate), encoding="utf-8")
    return TlsMaterialPaths(
        certificate=root / "server.crt",
        private_key=root / "server.key",
        client_ca=root / "ca.crt",
    )


def _material(root: Path, server_days: float, ca_days: float) -> TlsMaterialPaths:
    now = datetime.now(UTC)
    authority = _authority(now + timedelta(days=ca_days))
    return _write(root, _leaf(authority, now + timedelta(days=server_days)), authority)


@contextmanager
def _captured(level: str) -> Iterator[list[str]]:
    records: list[str] = []
    sink = logger.add(records.append, level=level)
    try:
        yield records
    finally:
        logger.remove(sink)


def test_expired_material_is_reported_as_an_error(tmp_path: Path) -> None:
    """已过期是 ERROR：此刻起没有任何 Worker 能建新连接。"""
    paths = _material(tmp_path, -EXPIRED_HOURS / 24, LONG_LIVED_DAYS)

    with _captured("ERROR") as records:
        assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRED

    assert any(TLS_MATERIAL_EXPIRED in record for record in records)


def test_material_inside_the_warning_window_is_reported_as_a_warning(tmp_path: Path) -> None:
    """预警窗口内必须出声——这就是热更新之外欠着的那半个问题。"""
    paths = _material(tmp_path, INSIDE_WINDOW_DAYS, LONG_LIVED_DAYS)

    with _captured("WARNING") as records:
        assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRING

    assert any(TLS_MATERIAL_EXPIRING in record for record in records)


def test_healthy_material_still_leaves_one_trace_per_tick(tmp_path: Path) -> None:
    """正常也要留痕：没有日志时，"一切正常"与"监控自己死了"长得一模一样。"""
    paths = _material(tmp_path, LONG_LIVED_DAYS, LONG_LIVED_DAYS)

    with _captured("INFO") as records:
        assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRY_OK

    assert any(TLS_MATERIAL_EXPIRY_OK in record for record in records)


def test_the_client_ca_expiry_is_watched_too(tmp_path: Path) -> None:
    """CA 过期比叶子证书过期更致命：它一次性废掉**每一个** Worker 的证书。

    只盯服务端证书的实现在这里恒绿，所以判据取的是两者里最早的那张。
    """
    paths = _material(tmp_path, LONG_LIVED_DAYS, INSIDE_WINDOW_DAYS)

    assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRING


def test_unreadable_material_is_reported_as_unknown_not_as_healthy(tmp_path: Path) -> None:
    """解析不出有效期就是"不知道"，必须按失败上报，绝不当成没问题。"""
    paths = _material(tmp_path, LONG_LIVED_DAYS, LONG_LIVED_DAYS)
    paths.certificate.write_bytes(b"-----BEGIN CERTIFICATE-----truncated")

    with _captured("ERROR") as records:
        assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRY_UNREADABLE

    assert any(TLS_MATERIAL_EXPIRY_UNREADABLE in record for record in records)


def test_expiry_checks_do_not_consume_the_hot_reload_fingerprint(tmp_path: Path) -> None:
    """到期监控不得吞掉一次待生效的热更新。

    ``TlsMaterialLoader.load()`` 顺手刷新内容指纹。监控若复用同一个 loader，它每小时
    一次的读盘就会把"材料变了"这条信息吃掉，gRPC 的握手回调随后看到的是"没变"，
    热更新从此永久失效——而且不会有任何报错。
    """
    paths = _material(tmp_path, LONG_LIVED_DAYS, LONG_LIVED_DAYS)
    loader = TlsMaterialLoader(paths)
    loader.load()

    rotated = _material(tmp_path / "rotated", LONG_LIVED_DAYS, LONG_LIVED_DAYS)
    paths.client_ca.write_bytes(rotated.client_ca.read_bytes())
    assert TlsExpiryMonitor(paths, _policy()).check_once() == TLS_MATERIAL_EXPIRY_OK

    assert loader.certificate_configuration() is not None


@pytest.mark.parametrize(("warning_seconds", "interval_seconds"), [(0, 1), (1, 0), (-1, 1)])
def test_non_positive_thresholds_are_rejected(warning_seconds: int, interval_seconds: int) -> None:
    with pytest.raises(ValueError):
        TlsExpiryPolicy(warning_seconds=warning_seconds, interval_seconds=interval_seconds)


def _gateway_config(monkeypatch: pytest.MonkeyPatch, paths: TlsMaterialPaths) -> GatewayConfig:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GRPC_TLS_CERT_PATH", str(paths.certificate))
    monkeypatch.setenv("GRPC_TLS_KEY_PATH", str(paths.private_key))
    monkeypatch.setenv("GRPC_TLS_CA_PATH", str(paths.client_ca))
    monkeypatch.setenv("GRPC_TLS_EXPIRY_CHECK_INTERVAL_SECONDS", str(CHECK_INTERVAL_SECONDS))
    monkeypatch.setenv("GRPC_TLS_EXPIRY_WARNING_DAYS", str(WARNING_DAYS))
    return GatewayConfig()


@pytest.mark.asyncio
async def test_gateway_startup_really_runs_the_monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写了模块没人调用等于没写——环境变量到 loop 这条链路整条钉住。"""
    paths = _material(tmp_path, LONG_LIVED_DAYS, LONG_LIVED_DAYS)
    running = asyncio.Event()

    class _Recording:
        def __init__(self, observed: TlsMaterialPaths, policy: TlsExpiryPolicy) -> None:
            assert observed == paths
            assert policy.interval_seconds == CHECK_INTERVAL_SECONDS
            assert policy.warning_seconds == WARNING_DAYS * SECONDS_PER_DAY

        async def run(self) -> None:
            running.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(gateway_main, "TlsExpiryMonitor", _Recording)
    task = gateway_main._start_tls_expiry_monitor(GrpcServer(_gateway_config(monkeypatch, paths)))

    assert task is not None
    await asyncio.wait_for(running.wait(), timeout=1)
    await gateway_main._stop_tls_expiry_monitor(task)
    assert task.done()


def test_a_plaintext_gateway_starts_no_monitor() -> None:
    """明文端口没有材料可查，起个恒失败的监控只会刷屏。"""
    assert gateway_main._start_tls_expiry_monitor(SimpleNamespace(config=SimpleNamespace(tls_enabled=False))) is None


class _ImmediateCoordinator:
    """让 ``main()`` 走完启动、立刻进入关闭，不用真的等信号。"""

    def __init__(self, _server: object) -> None: ...

    def install_signal_handlers(self) -> None: ...

    async def wait(self) -> None: ...

    async def shutdown(self, _signal: int | None = None) -> None: ...


@pytest.mark.asyncio
async def test_main_starts_the_monitor_and_stops_it_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """本仓最高发的 bug 是"写了没人调用"，所以 ``main()`` 这一跳也要单独钉住。"""
    events: list[str] = []
    handle = object()

    async def _stop(task: object) -> None:
        assert task is handle
        events.append("stop")

    for name, value in (
        ("setup_logging", lambda: None),
        ("init_db", AsyncMock()),
        ("get_grpc_server", lambda: SimpleNamespace(start=AsyncMock(return_value=True))),
        ("StreamClient", MagicMock()),
        ("_configure_interceptors", lambda *_: None),
        ("_create_lease_store", AsyncMock()),
        ("get_redis_client", AsyncMock(return_value=MagicMock())),
        ("reconcile_worker_lease_lifecycle_fences", AsyncMock()),
        ("_register_services", lambda *_args, **_kwargs: None),
        ("_ShutdownCoordinator", _ImmediateCoordinator),
        ("_start_tls_expiry_monitor", lambda _server: events.append("start") or handle),
        ("_stop_tls_expiry_monitor", _stop),
    ):
        monkeypatch.setattr(gateway_main, name, value)

    await gateway_main.main()

    assert events == ["start", "stop"]
