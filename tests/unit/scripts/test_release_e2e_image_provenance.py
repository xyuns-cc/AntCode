"""`[4/7]` 的镜像判据必须能区分"本轮构建的"与"上一轮遗留的"。

原判据对五个应用镜像**恒真**：期望值来自 `application_images(tag)`，Compose 侧写的是
`${ANTCODE_IMAGE_TAG}`，两边同源。真机上撞到过 `antcode-worker:gateway-e2e` 构建于被测
提交之前、镜像里新模块零命中——`SKIP_BUILD=1` 复用它，整轮 E2E 能对着旧代码报"7/7
全过"，当时靠人工发现，守卫结构上抓不住。

每条都配一正一反：镜像名一个字不变（旧判据两臂都过），换成另一张镜像必须被拒。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import release_e2e_orchestrator, release_e2e_provenance
from scripts.release_e2e_provenance import ImageIdentity
from tests.unit.scripts.release_e2e_support import (
    RUNTIMES,
    SERVICES,
    digest,
    environment_file,
    environment_values,
    run_prepare,
)

RUN_SCRIPT = Path("infra/docker/run-gateway-e2e.sh")
CONTROL_SERVICES = ("web-api", "master", "gateway", "frontend")
CREATED = "2026-08-25T12:50:05.123456789Z"
BUILT_ID = digest("1")
FOREIGN_ID = digest("2")
PREVIOUS_ROUND_IMAGE = "antcode-worker:previous-round"


def _fake_inspect(identifiers: dict[str, str]) -> Callable[[str], ImageIdentity]:
    def inspect(reference: str) -> ImageIdentity:
        return ImageIdentity(identifier=identifiers[reference], created=CREATED)

    return inspect


def _round(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    """跑一次 `[1/7]`，返回状态目录、镜像引用表与"本轮构建"的 image ID 表。"""
    state, _ = run_prepare(monkeypatch, tmp_path)
    references = json.loads((state / release_e2e_provenance.RELEASE_IMAGES_FILE).read_text(encoding="utf-8"))
    identifiers = {references[service]: BUILT_ID for service in SERVICES}
    monkeypatch.setattr(release_e2e_provenance, "inspect_image", _fake_inspect(identifiers))
    return state, references, identifiers


def test_image_ids_pin_the_round_while_the_name_check_stays_true_either_way(monkeypatch, tmp_path: Path) -> None:
    state, references, identifiers = _round(monkeypatch, tmp_path)
    compose_images = {service: references[service] for service in SERVICES}
    release_e2e_provenance.record(state, built_this_run=True)

    release_e2e_provenance.validate(state, compose_images)

    # 对照：旧判据比的是这两个名字，而它们同源——期望名来自 application_images(tag)，
    # Compose 侧是 ${ANTCODE_IMAGE_TAG}，tag 是同一个入参。所以它换没换镜像都恒真。
    tag = environment_values(state)["ANTCODE_IMAGE_TAG"]
    assert compose_images == {service: f"antcode-{service}:{tag}" for service in SERVICES}

    # 反臂：名字一个字没变，内容换成了另一张镜像。
    monkeypatch.setattr(release_e2e_provenance, "inspect_image", _fake_inspect(dict.fromkeys(identifiers, FOREIGN_ID)))
    with pytest.raises(RuntimeError, match="不是本轮构建"):
        release_e2e_provenance.validate(state, compose_images)


def test_orchestrator_rejects_both_an_unpinned_runtime_and_a_foreign_application_image(
    monkeypatch, tmp_path: Path
) -> None:
    state, references, identifiers = _round(monkeypatch, tmp_path)
    release_e2e_provenance.record(state, built_this_run=True)
    control = {name: {"image": references[name]} for name in (*RUNTIMES, *CONTROL_SERVICES)}
    worker = {"worker": {"image": references["worker"]}}

    def fake_run(command: list[str], *, capture: bool = False) -> str:
        services = worker if any("prod.worker.yml" in item for item in command) else control
        return json.dumps({"services": services})

    monkeypatch.setattr(release_e2e_orchestrator, "_run", fake_run)
    release_e2e_orchestrator._validate_exact_images(environment_file(state), state)

    control["postgres"]["image"] = f"registry.example/postgres@{digest('0')}"
    with pytest.raises(RuntimeError, match="production Compose image mismatch"):
        release_e2e_orchestrator._validate_exact_images(environment_file(state), state)

    control["postgres"]["image"] = references["postgres"]
    # 上一轮遗留的 Worker 镜像：仍是一张真实存在的镜像，只是不是本轮构建的那张。
    worker["worker"]["image"] = PREVIOUS_ROUND_IMAGE
    identifiers[PREVIOUS_ROUND_IMAGE] = FOREIGN_ID
    with pytest.raises(RuntimeError, match="不是本轮构建"):
        release_e2e_orchestrator._validate_exact_images(environment_file(state), state)


def test_reused_round_declares_itself_not_a_release_gate_and_names_what_it_reused(monkeypatch, tmp_path: Path) -> None:
    """SKIP_BUILD 有正当用途（省一次 4.6GB 重建），所以不废掉它，但它必须自报家门。"""
    state, _, _ = _round(monkeypatch, tmp_path)

    identities = release_e2e_provenance.record(state, built_this_run=False)

    reused = release_e2e_provenance._report(identities, built_this_run=False)
    built = release_e2e_provenance._report(identities, built_this_run=True)
    assert "不构成发布门禁" in reused
    assert "不构成发布门禁" not in built
    # 复用的是哪几张、什么时候构建的，必须原样打出来供人工判断。
    assert CREATED in reused
    assert BUILT_ID in reused
    payload = json.loads((state / release_e2e_provenance.PROVENANCE_FILE).read_text(encoding="utf-8"))
    assert payload["built_this_run"] is False


def test_run_script_records_provenance_on_both_paths_and_ends_with_a_verdict() -> None:
    lines = RUN_SCRIPT.read_text(encoding="utf-8").splitlines()
    statements = "\n".join(line for line in lines if not line.lstrip().startswith("#"))

    # 构建与跳过构建两条路径都要落一次记录，否则 [4/7] 无从比对。
    assert statements.count("python -m scripts.release_e2e_provenance") == 1
    assert "reused=(--reused)" in statements
    assert 'reused[@]+"${reused[@]}"' in statements
    # 复用轮次与完整构建轮次，"通过"的含义不同，最后一行必须说清楚是哪一种。
    assert "不构成发布门禁" in statements
    assert statements.rstrip().endswith("report_verdict")
