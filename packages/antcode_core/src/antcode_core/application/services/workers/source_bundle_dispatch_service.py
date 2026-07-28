"""Source bundle dispatch preparation for workers."""

from __future__ import annotations

from loguru import logger
from tortoise.transactions import in_transaction

from antcode_core.application.services.projects.source_bundle_service import (
    SourceBundleService,
    source_bundle_service,
)
from antcode_core.domain.models import Project, RunSourceSnapshot, Worker


class SourceBundleDispatchService:
    """Builds immutable source-bundle dispatch info for workers."""

    def __init__(self, bundle_service: SourceBundleService | None = None) -> None:
        self._bundle_service = bundle_service or source_bundle_service

    async def build_dispatch_for_worker_with_info(
        self,
        worker: Worker,
        project_ids: list[str],
        *,
        run_ids_by_project: dict[str, list[str]],
    ) -> tuple[dict[str, list], dict[str, dict]]:
        del worker
        from antcode_core.application.services.projects.project_sync_service import (
            project_sync_service,
        )

        results: dict[str, list] = {"synced": [], "skipped": [], "failed": []}
        dispatch_info: dict[str, dict] = {}
        project_map = await self._load_project_map(project_ids)

        for project_id in project_ids:
            project = project_map.get(project_id)
            if not project:
                self._append_failure(results, project_id, "项目不存在")
                continue
            try:
                transfer_info = await project_sync_service.get_project_transfer_info(
                    project.id,
                    project=project,
                )
                project_dispatch_info = await self._build_source_bundle_dispatch_infos(
                    project_public_id=project.public_id,
                    project_internal_id=project.id,
                    run_ids=run_ids_by_project.get(project.public_id, []),
                    transfer_info=transfer_info,
                )
                dispatch_info.update(project_dispatch_info)
                results["synced"].append(project_id)
            except Exception as exc:
                logger.error("构建 source bundle 失败 project={} err={}", project_id, exc)
                self._append_failure(results, project_id, str(exc))

        return results, dispatch_info

    async def _load_project_map(self, project_ids: list[str]) -> dict[str, Project]:
        project_map: dict[str, Project] = {}
        for project_id in set(project_ids):
            project = await Project.get_or_none(public_id=project_id)
            if project:
                project_map[project.public_id] = project
        return project_map

    async def _build_source_bundle_dispatch_info(
        self,
        project_public_id: str,
        project_internal_id: int,
        run_id: str,
        transfer_info: dict,
    ) -> dict[str, object]:
        dispatch_info = await self._build_source_bundle_dispatch_infos(
            project_public_id=project_public_id,
            project_internal_id=project_internal_id,
            run_ids=[run_id],
            transfer_info=transfer_info,
        )
        return dispatch_info[run_id]

    async def _build_source_bundle_dispatch_infos(
        self,
        *,
        project_public_id: str,
        project_internal_id: int,
        run_ids: list[str],
        transfer_info: dict,
    ) -> dict[str, dict[str, object]]:
        if transfer_info.get("transfer_method") != "source_bundle":
            raise ValueError("项目传输方式必须是 source_bundle")
        source = transfer_info.get("source")
        if not isinstance(source, dict):
            raise ValueError("source_bundle 缺少 Git 来源配置")
        unique_run_ids = list(dict.fromkeys(run_ids))
        if not unique_run_ids or any(not run_id for run_id in unique_run_ids):
            raise ValueError("run_id 不能为空，无法记录源码快照")
        dispatch_info, missing_run_ids = await self._load_existing_dispatch_info(
            project_internal_id,
            unique_run_ids,
        )
        if not missing_run_ids:
            return dispatch_info
        bundle = await self._bundle_service.create_git_source_bundle(
            project_public_id=project_public_id,
            source_config=source,
            entry_point=str(transfer_info.get("entry_point") or ""),
        )
        for run_id in missing_run_ids:
            snapshot, created = await self._record_run_source_snapshot(
                run_id=run_id,
                project_id=project_internal_id,
                source=source,
                bundle=bundle,
            )
            dispatch_info[run_id] = await self._new_snapshot_dispatch_info(
                snapshot,
                created=created,
                bundle=bundle,
                source_subdir=str(source.get("subdir") or ""),
                run_id=run_id,
                project_id=project_internal_id,
            )
        return dispatch_info

    async def _load_existing_dispatch_info(
        self,
        project_id: int,
        run_ids: list[str],
    ) -> tuple[dict[str, dict[str, object]], list[str]]:
        dispatch_info: dict[str, dict[str, object]] = {}
        missing_run_ids: list[str] = []
        for run_id in run_ids:
            snapshot = await RunSourceSnapshot.get_or_none(run_id=run_id, project_id=project_id)
            if snapshot is None:
                missing_run_ids.append(run_id)
                continue
            dispatch_info[run_id] = await self._dispatch_info_from_snapshot(snapshot)
        return dispatch_info, missing_run_ids

    async def _new_snapshot_dispatch_info(
        self,
        snapshot,
        *,
        created: bool,
        bundle,
        source_subdir: str,
        run_id: str,
        project_id: int,
    ) -> dict[str, object]:
        if created:
            return self._dispatch_info_from_bundle(bundle, source_subdir)
        logger.warning(
            "run 源码快照已存在(并发首写获胜)，改用已记录快照派发: run_id={} project_id={}",
            run_id,
            project_id,
        )
        return await self._dispatch_info_from_snapshot(snapshot)

    @staticmethod
    def _dispatch_info_from_bundle(bundle, source_subdir: str) -> dict[str, object]:
        return {
            "transfer_method": "source_bundle",
            "source_bundle_uri": bundle.uri,
            "source_bundle_sha256": bundle.sha256,
            "source_bundle_size": bundle.size_bytes,
            "source_subdir": source_subdir,
            "entry_point": bundle.entry_point,
            "resolved_revision": bundle.resolved_revision,
        }

    @staticmethod
    async def _dispatch_info_from_snapshot(snapshot: RunSourceSnapshot) -> dict[str, object]:
        """按已记录的不可变快照派发；artifact 缺失时显式失败（不静默重建）。"""
        from antcode_core.infrastructure.postgres.artifact_store import PostgresArtifactStore

        stored = await PostgresArtifactStore().stat_blob(snapshot.artifact_sha256)
        return {
            "transfer_method": "source_bundle",
            "source_bundle_uri": f"pgartifact://{snapshot.artifact_sha256}",
            "source_bundle_sha256": snapshot.artifact_sha256,
            "source_bundle_size": stored.size_bytes,
            "source_subdir": snapshot.subdir,
            "entry_point": snapshot.entry_point,
            "resolved_revision": snapshot.resolved_commit,
        }

    async def _record_run_source_snapshot(
        self,
        *,
        run_id: str,
        project_id: int,
        source: dict,
        bundle,
    ) -> tuple[RunSourceSnapshot, bool]:
        repository_id = source.get("repository_id")
        if repository_id is None:
            raise ValueError("source_bundle 缺少 repository_id")
        # P1-DB-02: get_or_create（非 update_or_create）——快照一经写入不可变。
        # entry_point 记录实际派发的 bundle entry，保证审计与派发一致。
        #
        # 复审 P1-DB-02: ``artifact_id`` 是无 FK 的 BigInt，与 artifact
        # cleanup 的 ``FOR UPDATE`` 之间此前没有任何锁交集 —— 复用旧
        # artifact（bundle 内容去重命中）时，cleanup 可在快照提交前删掉
        # metadata/chunks，留下悬空快照。这里在**同一事务**内先对
        # artifact 行取 ``FOR KEY SHARE``：与 cleanup 的 ``FOR UPDATE``
        # 互斥——cleanup 先到则本查询看到行已删、显式失败（调用方重建
        # bundle）；本事务先到则 cleanup 阻塞至快照提交，其 EvalPlanQual
        # 复评会看到新引用而放过该 artifact。
        async with in_transaction("default") as conn:
            _, rows = await conn.execute_query(
                "SELECT id FROM source_artifacts WHERE id = $1 FOR KEY SHARE",
                [int(bundle.artifact_id)],
            )
            if not rows:
                raise RuntimeError(f"source artifact 已被并发清理，无法记录快照: artifact_id={bundle.artifact_id}")
            return await RunSourceSnapshot.get_or_create(
                run_id=run_id,
                project_id=project_id,
                using_db=conn,
                defaults={
                    "repository_id": int(repository_id),
                    "artifact_id": int(bundle.artifact_id),
                    "artifact_sha256": bundle.sha256,
                    "resolved_commit": bundle.resolved_revision,
                    "subdir": str(source.get("subdir") or ""),
                    "entry_point": bundle.entry_point,
                    "include_paths": source.get("include_paths") or [],
                },
            )

    def _append_failure(self, results: dict[str, list], project_id: str, reason: str) -> None:
        results["failed"].append({"project_id": project_id, "reason": reason})


source_bundle_dispatch_service = SourceBundleDispatchService()
