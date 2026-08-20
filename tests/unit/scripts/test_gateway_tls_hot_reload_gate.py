"""容器级热更新校验自己也必须能证伪。

这道门禁的价值全在"它会不会漏掉假绿"。所以这里不验真容器，验的是**判据**：给它
一个坏掉的 Gateway，它必须红。四种坏法各对应本轮反复抓到的一种假绿形状：

- 凭证一次性读盘（改盘不生效）——只看"新证书能连"根本发现不了，因为旧的也能连；
- 指纹短路失效（每次握手都重读盘）——功能上看不出来，只有读盘次数会暴露；
- 靠重启换证——"新材料生效"这一条会绿，但卖点整个不成立；
- CA bundle 换了却新旧通吃——"新 CA 能连"会绿，吊销其实没发生。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from scripts import verify_gateway_tls_hot_reload as gate
from scripts.release_e2e_pki import write_release_pki
from scripts.release_e2e_tls_probe import ClientIdentity, ContainerIdentity

RUN_SCRIPT = Path("infra/docker/run-gateway-e2e.sh")
RUNNING = ContainerIdentity(pid="1252074", started_at="2026-08-20T06:27:12Z", restart_count="0")
RESTARTED = ContainerIdentity(pid="1252999", started_at="2026-08-20T06:41:03Z", restart_count="1")
#: 三次真实换料：重签服务端证书、换 CA bundle、还原。多一次说明材料没变也在重读盘。
EXPECTED_RELOADS = 3


@dataclass(frozen=True)
class _Behaviour:
    """假 Gateway 的毛病开关；全 False 就是热更新完全正常的那台。"""

    #: 凭证一次性读进 C core，改盘永不生效（这次修复之前的行为）。
    static_material: bool = False
    #: 指纹短路没了，每次握手都重新读盘。
    reload_every_handshake: bool = False
    #: 换料靠重启进程，而不是靠 fetcher 回调。
    restart_on_change: bool = False
    #: CA bundle 换了，旧 CA 签的客户端照收——吊销没有真的发生。
    trusts_every_authority: bool = False


class _FakeGateway:
    """按目录内容响应握手的假 Gateway，行为由 ``_Behaviour`` 决定。"""

    def __init__(self, directory: Path, behaviour: _Behaviour) -> None:
        self._directory = directory
        self._behaviour = behaviour
        self._loaded = self._on_disk()
        self._reloads = 0
        self._identity = RUNNING

    def _on_disk(self) -> dict[str, bytes]:
        return {name: (self._directory / name).read_bytes() for name in gate.MATERIAL_FILES}

    def _handshake(self) -> None:
        current = self._on_disk()
        if self._behaviour.reload_every_handshake:
            self._loaded = current
            self._reloads += 1
            return
        if self._behaviour.static_material or current == self._loaded:
            return
        self._loaded = current
        self._reloads += 1
        if self._behaviour.restart_on_change:
            self._identity = RESTARTED

    def served_certificate(self, _endpoint: tuple[str, int], _client: ClientIdentity) -> str:
        self._handshake()
        certificate = x509.load_pem_x509_certificate(self._loaded[gate.SERVER_CERTIFICATE])
        return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()

    def channel_becomes_ready(self, _endpoint: tuple[str, int], client: ClientIdentity) -> bool:
        self._handshake()
        if self._behaviour.trusts_every_authority:
            return True
        presented = x509.load_pem_x509_certificate(client.certificate.read_bytes())
        # 只比 issuer 名字不行：release PKI 的每套 CA 都叫同一个 CN，撞名会让"旧 CA
        # 被拒"这条判据恒绿。必须验签名。
        for authority in x509.load_pem_x509_certificates(self._loaded[gate.CLIENT_CA]):
            try:
                presented.verify_directly_issued_by(authority)
            except (InvalidSignature, TypeError, ValueError):
                continue
            return True
        return False

    def reload_count(self, _container: str) -> int:
        return self._reloads

    def container_identity(self, _container: str) -> ContainerIdentity:
        return self._identity


def _arguments(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        gateway_tls_dir=root / "gateway-tls",
        ca_cert=root / "public-ca.crt",
        ca_key=root / "ca.key",
        client_cert=root / "worker-tls/client.crt",
        client_key=root / "worker-tls/client.key",
        gateway_container="antcode-release-control-gateway",
        gateway_host="localhost",
        gateway_port=15051,
    )


def _prepare(root: Path) -> tuple[gate._Probe, gate._Authorities]:
    write_release_pki(root)
    scratch = root / "scratch"
    scratch.mkdir()
    return gate._build_probe(_arguments(root), scratch)


def _run(setup: tuple[gate._Probe, gate._Authorities], behaviour: _Behaviour, patch: pytest.MonkeyPatch) -> None:
    probe, authorities = setup
    gateway = _FakeGateway(probe.directory, behaviour)
    for name in ("served_certificate", "channel_becomes_ready", "reload_count", "container_identity"):
        patch.setattr(gate, name, getattr(gateway, name))
    gate._verify(probe, authorities)
    assert gateway.reload_count("") == EXPECTED_RELOADS


def test_a_gateway_that_really_hot_reloads_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run(_prepare(tmp_path), _Behaviour(), monkeypatch)


@dataclass(frozen=True)
class _FalseGreen:
    """一种假绿形状：这样的 Gateway 必须让门禁红在 ``failure`` 这句判据上。"""

    behaviour: _Behaviour
    failure: str


@pytest.mark.parametrize(
    "shape",
    [
        _FalseGreen(_Behaviour(static_material=True), "kept serving the previous certificate"),
        _FalseGreen(_Behaviour(reload_every_handshake=True), "re-read per handshake"),
        _FalseGreen(_Behaviour(restart_on_change=True), "restarted during the rotation"),
        _FalseGreen(_Behaviour(trusts_every_authority=True), "still accepted"),
    ],
)
def test_every_false_green_shape_is_caught(
    shape: _FalseGreen,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match=shape.failure):
        _run(_prepare(tmp_path), shape.behaviour, monkeypatch)


def test_material_is_restored_even_when_the_gate_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁失败也必须把材料写回去，否则后续 E2E 场景里的 Worker 会全部连不上。"""
    setup = _prepare(tmp_path)
    directory = setup[0].directory
    before = {name: (directory / name).read_bytes() for name in gate.MATERIAL_FILES}

    with pytest.raises(RuntimeError):
        _run(setup, _Behaviour(trusts_every_authority=True), monkeypatch)

    assert {name: (directory / name).read_bytes() for name in gate.MATERIAL_FILES} == before


def test_the_release_e2e_script_actually_runs_the_hot_reload_gate() -> None:
    """校验存在但没人调用，等于没有——本仓已经栽过好几次。"""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert "python -m scripts.verify_gateway_tls_hot_reload" in script
    assert script.index("verify_tls_hot_reload\n") < script.index("run_e2e\n")
