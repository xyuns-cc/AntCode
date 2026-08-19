"""多 Worker 发布 E2E 机群的编排契约。

历史上编排器只允许"恰好一个新 Worker 注册"，多节点 mTLS 因此从未被验证过。
这里锁住替代方案的关键不变量：每个 Worker 独占一份身份材料，身份来源是它自己
持久化的凭据，控制台侧仍然拒绝任何没人认领的额外注册。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID

from scripts import release_e2e_compose, release_e2e_environment, release_e2e_workers
from scripts.release_e2e_pki import write_release_pki, write_worker_identity_certificate

RUN_SCRIPT = Path("infra/docker/run-gateway-e2e.sh")
WORKER_COUNT = 3
BASELINE: set[str] = {"pre-existing"}


def _fleet(tmp_path: Path, count: int = WORKER_COUNT) -> release_e2e_workers.Fleet:
    return release_e2e_workers.Fleet(
        environment=tmp_path / "production.env",
        state_dir=tmp_path,
        slug="local-gateway-e2e",
        count=count,
    )


def _common_name(path: Path) -> str:
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    return str(certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)


def test_each_worker_gets_its_own_container_volume_and_identity_material(tmp_path: Path) -> None:
    """共用容器名/数据卷/证书目录的任何一项都会让第二个 Worker 起不来或被 CN 绑定拒绝。"""
    variables = [release_e2e_environment.worker_variables(tmp_path, "slug", index) for index in range(WORKER_COUNT)]

    for name in (
        "ANTCODE_WORKER_NAME",
        "ANTCODE_WORKER_CONTAINER_NAME",
        "ANTCODE_WORKER_DATA_VOLUME",
        "ANTCODE_WORKER_TLS_DIR",
        "ANTCODE_WORKER_INSTALL_KEY_FILE",
    ):
        assert len({item[name] for item in variables}) == WORKER_COUNT, name
    # index 0 不加后缀：单 Worker 拓扑的命名必须与既有部署逐字一致。
    assert variables[0]["ANTCODE_WORKER_CONTAINER_NAME"] == "antcode-slug-worker"
    assert variables[0]["ANTCODE_WORKER_TLS_DIR"] == str(tmp_path / "worker-tls")


def test_release_pki_issues_one_bootstrap_client_certificate_per_worker(tmp_path: Path) -> None:
    directories = [release_e2e_environment.worker_tls_directory(index) for index in range(WORKER_COUNT)]
    write_release_pki(tmp_path, worker_directories=directories)

    ca = x509.load_pem_x509_certificate((tmp_path / "public-ca.crt").read_bytes())
    for directory in directories:
        client = x509.load_pem_x509_certificate((tmp_path / directory / "client.crt").read_bytes())
        client.verify_directly_issued_by(ca)
        assert (tmp_path / directory / "ca.crt").exists()


def test_identity_certificate_rotation_touches_only_the_target_worker(tmp_path: Path) -> None:
    """重签必须按目录隔离——写串了会把另一个 Worker 的身份换掉，CN 绑定当场拒它。"""
    directories = [release_e2e_environment.worker_tls_directory(index) for index in range(2)]
    write_release_pki(tmp_path, worker_directories=directories)
    untouched = (tmp_path / directories[0] / "client.crt").read_bytes()

    write_worker_identity_certificate(tmp_path, "worker-b", directory=directories[1])

    assert (tmp_path / directories[0] / "client.crt").read_bytes() == untouched
    assert _common_name(tmp_path / directories[1] / "client.crt") == "worker-b"


def test_worker_compose_projects_are_distinct_and_index_zero_keeps_the_legacy_name() -> None:
    projects = [release_e2e_compose.worker_project(index) for index in range(WORKER_COUNT)]

    assert projects[0] == release_e2e_compose.WORKER_PROJECT
    assert len(set(projects)) == WORKER_COUNT


def test_run_script_derives_worker_projects_and_env_files_the_same_way_as_python() -> None:
    """清理脚本与编排器一旦对不上，多起的 Worker 容器与数据卷就会残留在测试机上。"""
    script = RUN_SCRIPT.read_text(encoding="utf-8")

    assert f'WORKER_PROJECT="{release_e2e_compose.WORKER_PROJECT}"' in script
    assert 'project="$WORKER_PROJECT-$index"' in script
    assert re.search(r'\.\s+"\$STATE_DIR/worker-\$index\.env"', script)
    assert release_e2e_environment.worker_env_file(1) == "worker-1.env"


def test_collect_identities_rejects_two_workers_claiming_the_same_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """两个容器自报同一个 worker_id 意味着凭据被复用，绝不能当成"两个 Worker"放过。"""
    monkeypatch.setattr(release_e2e_workers, "_await_identity", lambda _fleet, _index: "same-id")

    with pytest.raises(RuntimeError, match="duplicate identities"):
        release_e2e_workers.collect_identities(_fleet(tmp_path))


class _StubClient:
    """按调用轮次返回控制台 Worker 列表。"""

    def __init__(self, rounds: list[list[str]]) -> None:
        self.rounds = rounds


async def _stub_get_workers(client: _StubClient, _token: str) -> list[dict[str, Any]]:
    identifiers = client.rounds.pop(0) if len(client.rounds) > 1 else client.rounds[0]
    return [{"id": identifier} for identifier in [*BASELINE, *identifiers]]


@pytest.mark.asyncio
async def test_console_registration_waits_for_every_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_e2e_workers, "get_workers", _stub_get_workers)
    monkeypatch.setattr(release_e2e_workers, "WORKER_REGISTRATION_POLL_SECONDS", 0)
    client = _StubClient([["w0"], ["w0", "w1"]])

    await release_e2e_workers.verify_console_registration(client, "token", BASELINE, expected=["w0", "w1"])


@pytest.mark.asyncio
async def test_console_registration_rejects_a_worker_nobody_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """原编排器"多于一个新 Worker 就报错"的判据，在多节点下的正确推广形态。"""
    monkeypatch.setattr(release_e2e_workers, "get_workers", _stub_get_workers)
    client = _StubClient([["w0", "w1", "intruder"]])

    with pytest.raises(RuntimeError, match="unexpected Workers registered"):
        await release_e2e_workers.verify_console_registration(client, "token", BASELINE, expected=["w0", "w1"])
