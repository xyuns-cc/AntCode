"""项目声明的 language 必须真的进派发参数。

修复前全仓没有任何一处写 ``kwargs["language"]``，CodePlugin 里那段"显式 language
优先"的分支是死的，实际路由只剩 entry_point 后缀：界面选 TypeScript 而入口写 .js
就按 JS 跑，选 Java 而入口无后缀就按 Python 跑并报 python_path 缺失。

本文件按链路逐段钉死：项目详情 → transfer_info → run 级 dispatch_info →
任务帧 params.kwargs.language。任何一段断掉都要变红。
"""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.application.services.projects.project_source_service import ProjectSourceService
from antcode_core.application.services.workers.dispatch_task_enrichment import enrich_dispatch_tasks
from antcode_core.application.services.workers.source_bundle_dispatch_service import (
    SourceBundleDispatchService,
)
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher

_SERVICE_MODULE = "antcode_core.application.services.workers.source_bundle_dispatch_service"


def _detail(entry_point: str, language: str) -> SimpleNamespace:
    return SimpleNamespace(entry_point=entry_point, language=language)


def _source() -> SimpleNamespace:
    return SimpleNamespace(repository_id=11, ref="main", subdir="svc", include_paths=[])


@pytest.mark.asyncio
async def test_transfer_info_carries_language_from_project_detail():
    service = ProjectSourceService()
    with (
        patch.object(service, "get_source", AsyncMock(return_value=_source())),
        patch.object(
            service,
            "_get_repository",
            AsyncMock(return_value=SimpleNamespace(id=11, url="https://example.com/r.git", credential_id=None)),
        ),
        patch.object(service, "_get_source_detail", AsyncMock(return_value=_detail("cmd/main.go", "go"))),
    ):
        info = await service.get_transfer_info(22)

    assert info["language"] == "go"


@pytest.mark.asyncio
async def test_transfer_info_rejects_language_contradicting_entry_point():
    """矛盾的项目在 Master 侧就失败，不会产出一份用错解释器的执行计划。"""
    service = ProjectSourceService()
    with (
        patch.object(service, "get_source", AsyncMock(return_value=_source())),
        patch.object(
            service,
            "_get_repository",
            AsyncMock(return_value=SimpleNamespace(id=11, url="https://example.com/r.git", credential_id=None)),
        ),
        patch.object(service, "_get_source_detail", AsyncMock(return_value=_detail("main.py", "java"))),
    ):
        with pytest.raises(ValueError, match="请把两者改到一致"):
            await service.get_transfer_info(22)


@pytest.mark.asyncio
async def test_reused_run_snapshot_still_carries_language():
    """快照分支不经过 bundle 构建；language 取自当次 transfer_info 而非快照列。"""
    service = SourceBundleDispatchService(object())
    snapshot = SimpleNamespace(
        artifact_sha256="c" * 64,
        resolved_commit="d" * 40,
        subdir="spiders/news",
        entry_point="main.js",
    )

    with (
        patch(
            f"{_SERVICE_MODULE}.RunSourceSnapshot.get_or_none",
            AsyncMock(return_value=snapshot),
        ),
        patch(
            "antcode_core.infrastructure.postgres.artifact_store.PostgresArtifactStore.stat_blob",
            AsyncMock(return_value=SimpleNamespace(size_bytes=7)),
        ),
    ):
        info = await service._build_source_bundle_dispatch_infos(
            project_public_id="proj-1",
            project_internal_id=1,
            run_ids=["run-old"],
            transfer_info={
                "transfer_method": "source_bundle",
                "source": {"repository_id": 7, "subdir": "spiders/news"},
                "entry_point": "main.js",
                "language": "javascript",
            },
        )

    assert info["run-old"]["language"] == "javascript"


@pytest.mark.asyncio
async def test_dispatch_info_without_language_is_refused_not_skipped():
    service = SourceBundleDispatchService(object())

    with patch(f"{_SERVICE_MODULE}.RunSourceSnapshot.get_or_none", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="缺少执行语言"):
            await service._build_source_bundle_dispatch_infos(
                project_public_id="proj-1",
                project_internal_id=1,
                run_ids=["run-1"],
                transfer_info={
                    "transfer_method": "source_bundle",
                    "source": {"repository_id": 7, "subdir": ""},
                    "entry_point": "main.py",
                },
            )


def test_enriched_task_params_carry_language_for_the_worker():
    tasks = [{"task_id": "run-1", "params": {"args": ["--fast"]}}]

    enriched = enrich_dispatch_tasks(tasks, {"run-1": {"language": "typescript"}})

    assert enriched[0]["params"]["kwargs"]["language"] == "typescript"
    assert enriched[0]["params"]["args"] == ["--fast"]


def test_task_supplied_language_is_overwritten_by_the_project_language():
    """language 是项目级配置：单次任务的 execution_params 不得分叉出第二个真源。"""
    tasks = [{"task_id": "run-1", "params": {"kwargs": {"language": "go", "keep": 1}}}]

    enriched = enrich_dispatch_tasks(tasks, {"run-1": {"language": "python"}})

    assert enriched[0]["params"]["kwargs"] == {"language": "python", "keep": 1}


def test_enrichment_does_not_mutate_the_caller_params():
    original = {"kwargs": {"keep": 1}}
    tasks = [{"task_id": "run-1", "params": original}]

    enrich_dispatch_tasks(tasks, {"run-1": {"language": "python"}})

    assert original == {"kwargs": {"keep": 1}}


def test_missing_language_in_dispatch_info_refuses_the_dispatch():
    with pytest.raises(ValueError, match="缺少执行语言"):
        enrich_dispatch_tasks([{"task_id": "run-1"}], {"run-1": {"entry_point": "main.py"}})


@pytest.mark.asyncio
async def test_published_task_frame_carries_the_project_language(monkeypatch):
    """整条链的收口断言：真正写进 ready 消息的那份 params 里必须有 language。"""
    dispatcher = WorkerTaskDispatcher()
    worker = SimpleNamespace(
        id=7,
        public_id="worker-7",
        name="Worker 7",
        transport_mode="direct",
        capabilities={"task_types": ["code"]},
    )
    bundle_dispatch = SimpleNamespace(
        build_dispatch_for_worker_with_info=AsyncMock(
            return_value=(
                {"synced": ["proj-1"], "skipped": [], "failed": []},
                {"run-1": {"entry_point": "app-runner", "language": "java"}},
            )
        )
    )
    published: list[dict] = []

    async def publish(*, tasks, **_kwargs):
        published.extend(tasks)
        return {"success": True, "accepted_count": 1, "rejected_count": 0}

    dispatcher._select_worker = AsyncMock(return_value=worker)
    dispatcher._ensure_worker_connected = AsyncMock(return_value=True)
    dispatcher._bind_task_runs_to_worker = AsyncMock(return_value=1)
    dispatcher._send_batch_to_queue = publish
    monkeypatch.setattr(
        "antcode_core.application.services.workers.worker_dispatcher.require_worker_current_requirements",
        AsyncMock(return_value=LeaseCapabilitySnapshot("lease-7", '{"task_types":["code"]}', 7)),
    )
    monkeypatch.setattr(
        import_module(_SERVICE_MODULE),
        "source_bundle_dispatch_service",
        bundle_dispatch,
    )

    result = await dispatcher.dispatch_batch(
        tasks=[{"task_id": "run-1", "project_id": "proj-1", "project_type": "code"}]
    )

    assert result.success is True
    assert published[0]["params"]["kwargs"]["language"] == "java"
    assert published[0]["entry_point"] == "app-runner"
