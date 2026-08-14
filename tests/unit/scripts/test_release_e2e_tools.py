import ipaddress
import json
import ssl
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import yaml
from antcode_core.common.config import Settings
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from scripts import (
    prepare_local_release_e2e,
    prepare_release_e2e,
    release_e2e_environment,
    release_e2e_orchestrator,
    verify_release_transport,
)
from scripts.release_e2e_pki import write_worker_identity_certificate

SERVICES = ("web-api", "master", "gateway", "worker", "frontend")
RUNTIMES = ("postgres", "redis", "reverse-proxy")
REVISION = "a" * 40
IMAGE_PREFIX = "ghcr.io/example/antcode"
EXPECTED_PRIVATE_FILE_MODE = 0o600


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _write_descriptors(root: Path) -> Path:
    digest_dir = root / "digests"
    digest_dir.mkdir()
    for offset, service in enumerate(SERVICES, start=1):
        index = _digest(str(offset))
        (digest_dir / f"{service}.digest").write_text(index, encoding="utf-8")
        descriptor = {
            "schema_version": 1,
            "index": index,
            "amd64": _digest("b"),
            "arm64": _digest("c"),
        }
        (digest_dir / f"{service}.platforms.json").write_text(json.dumps(descriptor), encoding="utf-8")
    return digest_dir


def _write_runtime_lock(root: Path) -> Path:
    lock = root / "runtime.json"
    images = {name: f"registry.example/{name}@{_digest('d')}" for name in RUNTIMES}
    lock.write_text(json.dumps({"schema_version": 1, "images": images}), encoding="utf-8")
    return lock


def _args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        output_dir=root / "state",
        digest_dir=_write_descriptors(root),
        runtime_lock=_write_runtime_lock(root),
        image_prefix=IMAGE_PREFIX,
        revision=REVISION,
        repository="example/antcode",
    )


def _run_prepare(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[Path, Path]:
    args = _args(root)
    github_env = root / "github.env"
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_release_e2e",
            "--output-dir",
            str(args.output_dir),
            "--digest-dir",
            str(args.digest_dir),
            "--runtime-lock",
            str(args.runtime_lock),
            "--image-prefix",
            args.image_prefix,
            "--revision",
            args.revision,
            "--repository",
            args.repository,
        ],
    )
    prepare_release_e2e.main()
    return args.output_dir, github_env


def test_prepare_material_is_ephemeral_path_only_and_exports_complete_e2e_env(monkeypatch, tmp_path: Path) -> None:
    state, github_env = _run_prepare(monkeypatch, tmp_path)
    production_env = (state / "production.env").read_text(encoding="utf-8")
    exports = github_env.read_text(encoding="utf-8")

    assert all(state in path.parents for path in state.rglob("*"))
    assert "ANTCODE_E2E_ADMIN_USER=admin" in exports
    assert "ANTCODE_E2E_WEB_API_URL=https://localhost" in exports
    assert "ANTCODE_E2E_EXPECT_TRANSPORT_MODE=gateway" in exports
    assert "ANTCODE_E2E_REDIS_URL=redis://" in exports
    assert "ANTCODE_E2E_REDIS_NAMESPACE=antcode" in exports
    assert "DATABASE_URL=" not in exports
    assert "ANTCODE_E2E_WORKER_ID" not in exports
    for secret_file in (state / "secrets").iterdir():
        secret = secret_file.read_text(encoding="utf-8")
        if secret:
            assert secret not in production_env
    images = json.loads((state / "release-images.json").read_text(encoding="utf-8"))
    assert set(images) == {*SERVICES, *RUNTIMES}
    assert stat.S_IMODE((state / "ca.key").stat().st_mode) == EXPECTED_PRIVATE_FILE_MODE
    values = dict(line.split("=", maxsplit=1) for line in production_env.splitlines())
    edge_subnet = ipaddress.ip_network(values["ANTCODE_EDGE_SUBNET"])
    assert ipaddress.ip_address(values["ANTCODE_REVERSE_PROXY_EDGE_IP"]) != next(edge_subnet.hosts())


def test_release_pki_has_expected_ca_san_eku_and_rotated_worker_identity(monkeypatch, tmp_path: Path) -> None:
    state, _ = _run_prepare(monkeypatch, tmp_path)
    ca = x509.load_pem_x509_certificate((state / "public-ca.crt").read_bytes())
    gateway = x509.load_pem_x509_certificate((state / "gateway-tls/server.crt").read_bytes())
    bootstrap = x509.load_pem_x509_certificate((state / "worker-tls/client.crt").read_bytes())

    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    assert {str(name) for name in gateway.extensions.get_extension_for_class(x509.SubjectAlternativeName).value} >= {
        "<DNSName(value='localhost')>",
        "<DNSName(value='host.docker.internal')>",
    }
    assert gateway.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.SERVER_AUTH]
    )
    assert bootstrap.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value == x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.CLIENT_AUTH]
    )
    gateway.verify_directly_issued_by(ca)
    bootstrap.verify_directly_issued_by(ca)

    write_worker_identity_certificate(state, "worker-123")
    rotated = x509.load_pem_x509_certificate((state / "worker-tls/client.crt").read_bytes())
    assert rotated.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "worker-123"
    assert rotated.fingerprint(rotated.signature_hash_algorithm) != bootstrap.fingerprint(
        bootstrap.signature_hash_algorithm
    )
    rotated.verify_directly_issued_by(ca)


def test_application_descriptor_set_and_platform_mapping_are_fail_closed(tmp_path: Path) -> None:
    args = _args(tmp_path)
    images = prepare_release_e2e._application_images(args)
    assert set(images) == set(SERVICES)

    (args.digest_dir / "unexpected.digest").write_text(_digest("e"), encoding="utf-8")
    with pytest.raises(ValueError, match="exact five-service"):
        prepare_release_e2e._application_images(args)


@pytest.mark.parametrize("reference", ["postgres:16", "postgres@sha256:not-hex", f"postgres@{_digest('A')}"])
def test_runtime_image_lock_rejects_noncanonical_digest(tmp_path: Path, reference: str) -> None:
    lock = _write_runtime_lock(tmp_path)
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["images"]["postgres"] = reference
    lock.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="not digest locked"):
        prepare_release_e2e._runtime_images(lock)


def test_release_orchestrator_exact_image_validation_rejects_mismatch(monkeypatch, tmp_path: Path) -> None:
    expected = {name: f"registry.example/{name}@{_digest('f')}" for name in (*SERVICES, *RUNTIMES)}
    (tmp_path / "release-images.json").write_text(json.dumps(expected), encoding="utf-8")
    control = {name: {"image": expected[name]} for name in (*RUNTIMES, "web-api", "master", "gateway", "frontend")}
    worker = {"worker": {"image": expected["worker"]}}

    def fake_run(command: list[str], *, capture: bool = False) -> str:
        services = worker if any("prod.worker.yml" in item for item in command) else control
        return json.dumps({"services": services})

    monkeypatch.setattr(release_e2e_orchestrator, "_run", fake_run)
    release_e2e_orchestrator._validate_exact_images(tmp_path / "production.env", tmp_path)
    worker["worker"]["image"] = f"registry.example/worker@{_digest('0')}"
    with pytest.raises(RuntimeError, match="Worker image mismatch"):
        release_e2e_orchestrator._validate_exact_images(tmp_path / "production.env", tmp_path)


def test_ci_compose_overrides_preserve_production_tls_and_worker_sandbox() -> None:
    control = yaml.safe_load(Path("infra/docker/docker-compose.prod.ci-control.yml").read_text(encoding="utf-8"))
    worker_override = yaml.safe_load(Path("infra/docker/docker-compose.prod.ci-worker.yml").read_text(encoding="utf-8"))
    worker = yaml.safe_load(Path("infra/docker/docker-compose.prod.worker.yml").read_text(encoding="utf-8"))[
        "services"
    ]["worker"]

    assert set(control["services"]) == {"postgres", "redis", "master", "web-api"}
    assert set(worker_override["services"]) == {"worker"}
    assert worker_override["services"]["worker"]["environment"] == {"SSL_CERT_FILE": "/etc/antcode/tls/ca.crt"}
    assert worker["environment"]["WORKER_GATEWAY_TLS"] == "true"
    assert worker["environment"]["WORKER_SANDBOX_MODE"] == "sandbox"
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]


def test_login_rate_limit_is_relaxed_only_in_the_ci_overlay() -> None:
    """生产默认 5 次/分钟·IP 会让一轮 E2E 从第 6 次登录起恒 429；放宽只许出现在 CI overlay。"""
    control = yaml.safe_load(Path("infra/docker/docker-compose.prod.ci-control.yml").read_text(encoding="utf-8"))
    relaxed = control["services"]["web-api"]["environment"]

    assert int(relaxed["LOGIN_RATE_LIMIT_IP_MAX"]) > Settings().LOGIN_RATE_LIMIT_IP_MAX
    assert int(relaxed["LOGIN_RATE_LIMIT_USER_MAX"]) > Settings().LOGIN_RATE_LIMIT_USER_MAX
    production = (
        "infra/docker/docker-compose.prod.yml",
        "infra/docker/docker-compose.prod.control.yml",
        "infra/docker/docker-compose.prod.middleware.yml",
        "infra/docker/docker-compose.prod.edge.yml",
        "infra/docker/docker-compose.prod.worker.yml",
    )
    for name in production:
        assert "LOGIN_RATE_LIMIT" not in Path(name).read_text(encoding="utf-8")


def test_e2e_git_base_url_is_routable_from_both_host_runner_and_containers() -> None:
    """host.docker.internal 只在容器里解析，宿主上的 pytest 解析不了，必须用宿主出口 IP。"""
    base_url = release_e2e_environment.host_git_base_url()
    host = urlsplit(base_url).hostname or ""

    assert release_e2e_environment.CONTAINER_HOST_ALIAS not in base_url
    assert ipaddress.ip_address(host)
    assert urlsplit(base_url).port == release_e2e_environment.GIT_HTTP_PORT


def test_local_image_resolution_is_fail_closed_without_a_unique_repo_digest(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        captured.append(command[3])
        return SimpleNamespace(stdout=json.dumps(digests))

    monkeypatch.setattr(prepare_local_release_e2e.subprocess, "run", fake_run)
    digests = [f"antcode-web-api@{_digest('a')}"]
    assert prepare_local_release_e2e._local_digest_reference("web-api", "gateway-e2e").endswith(_digest("a"))
    assert captured == ["antcode-web-api:gateway-e2e"]

    digests = []
    with pytest.raises(RuntimeError, match="RepoDigest"):
        prepare_local_release_e2e._local_digest_reference("web-api", "gateway-e2e")
    digests = ["antcode-web-api:gateway-e2e"]
    with pytest.raises(RuntimeError, match="规范 digest"):
        prepare_local_release_e2e._local_digest_reference("web-api", "gateway-e2e")


def test_security_header_probe_rejects_duplicates_not_just_absence() -> None:
    """三层各设一遍会让客户端收到三份；多值 X-Frame-Options 按 RFC 7034 无效，必须判失败。"""
    expected = list(verify_release_transport.EXACTLY_ONCE_SECURITY_HEADERS.items())
    verify_release_transport._verify_security_headers(httpx.Response(200, headers=expected))

    duplicated = [*expected, ("x-content-type-options", "nosniff")]
    with pytest.raises(RuntimeError, match="exactly once"):
        verify_release_transport._verify_security_headers(httpx.Response(200, headers=duplicated))
    with pytest.raises(RuntimeError, match="exactly once"):
        verify_release_transport._verify_security_headers(httpx.Response(200, headers=expected[1:]))


def test_transport_probe_requires_both_modern_tls_versions_and_rejects_legacy(monkeypatch) -> None:
    negotiated = {
        ssl.TLSVersion.TLSv1_2: "TLSv1.2",
        ssl.TLSVersion.TLSv1_3: "TLSv1.3",
    }
    calls: list[ssl.TLSVersion] = []

    def fake_negotiate(_args, version: ssl.TLSVersion) -> str:
        calls.append(version)
        if version not in negotiated:
            raise ssl.SSLError("unsupported protocol")
        return negotiated[version]

    monkeypatch.setattr(verify_release_transport, "_negotiate_tls", fake_negotiate)
    verify_release_transport._verify_tls_versions(SimpleNamespace())
    assert calls == [
        ssl.TLSVersion.TLSv1_2,
        ssl.TLSVersion.TLSv1_3,
        ssl.TLSVersion.TLSv1,
        ssl.TLSVersion.TLSv1_1,
    ]
