"""节点项目分发服务 - 管理分布式同步状态"""

from datetime import datetime

from loguru import logger
from tortoise.expressions import Q

from antcode_core.domain.models import WorkerProject, WorkerProjectFile


class WorkerProjectService:
    """节点项目分发服务"""

    async def check_worker_has_project(self, worker_id, project_public_id):
        """检查 Worker 项目状态"""
        return await WorkerProject.get_or_none(worker_id=worker_id, project_public_id=project_public_id)

    async def is_project_outdated(self, worker_id, project_public_id, current_hash):
        """验证项目版本是否过期"""
        worker_project = await self.check_worker_has_project(worker_id, project_public_id)

        if not worker_project:
            return True

        if worker_project.file_hash != current_hash:
            logger.info(
                f"项目版本过期 [{project_public_id}@Worker {worker_id}] "
                f"本地:{worker_project.file_hash[:8]} 最新:{current_hash[:8]}"
            )
            return True

        return False

    async def record_project_sync(
        self,
        worker_id,
        project_id,
        project_public_id,
        file_hash,
        file_size,
        transfer_method,
        worker_local_project_id=None,
        metadata=None,
    ):
        """记录同步状态"""
        worker_project = await WorkerProject.get_or_none(
            worker_id=worker_id, project_public_id=project_public_id
        )

        if worker_project:
            worker_project.file_hash = file_hash
            worker_project.file_size = file_size
            worker_project.transfer_method = transfer_method
            worker_project.status = "synced"
            worker_project.sync_count += 1
            if worker_local_project_id:
                worker_project.worker_local_project_id = worker_local_project_id
            if metadata:
                worker_project.metadata = metadata
            await worker_project.save()

            logger.info(
                f"同步记录已更新 [Worker {worker_id}@{project_public_id}] "
                f"同步次数:{worker_project.sync_count}"
            )
        else:
            worker_project = await WorkerProject.create(
                worker_id=worker_id,
                project_id=project_id,
                project_public_id=project_public_id,
                worker_local_project_id=worker_local_project_id,
                file_hash=file_hash,
                file_size=file_size,
                transfer_method=transfer_method,
                status="synced",
                metadata=metadata,
            )

            logger.info(f"同步记录已创建 [Worker {worker_id}@{project_public_id}]")

        return worker_project

    async def mark_project_used(self, worker_id, project_public_id):
        """更新项目使用时间"""
        worker_project = await self.check_worker_has_project(worker_id, project_public_id)
        if worker_project:
            worker_project.last_used_at = datetime.now()
            await worker_project.save()

    async def mark_project_outdated(self, project_public_id):
        """标记项目过期"""
        await WorkerProject.filter(project_public_id=project_public_id).update(status="outdated")

        logger.info(f"项目已标记过期 [{project_public_id}]")

    async def get_worker_projects(self, worker_id, status=None):
        """查询 Worker 项目列表"""
        query = WorkerProject.filter(worker_id=worker_id)
        if status:
            query = query.filter(status=status)
        return await query.all()

    async def get_project_workers(self, project_public_id, status=None):
        """查询项目分发 Worker"""
        query = WorkerProject.filter(project_public_id=project_public_id)
        if status:
            query = query.filter(status=status)
        return await query.all()

    async def delete_worker_project(self, worker_id, project_public_id):
        """删除同步记录（级联删除文件记录）"""
        # 先获取 WorkerProject 记录
        worker_project = await WorkerProject.filter(
            worker_id=worker_id, project_public_id=project_public_id
        ).first()

        if not worker_project:
            return False

        # 删除关联的文件记录
        await WorkerProjectFile.filter(worker_project_id=worker_project.id).delete()

        # 删除 WorkerProject 记录
        await worker_project.delete()

        return True

    async def cleanup_outdated_records(self, days=30):
        """清理过期记录（级联删除文件记录）"""
        from datetime import timedelta

        cutoff_date = datetime.now() - timedelta(days=days)

        # 先获取要删除的 WorkerProject 记录
        outdated_projects = await WorkerProject.filter(
            Q(status="outdated") & (Q(last_used_at__lt=cutoff_date) | Q(last_used_at__isnull=True))
        ).all()

        if not outdated_projects:
            return 0

        # 删除关联的文件记录
        np_ids = [np.id for np in outdated_projects]
        files_deleted = await WorkerProjectFile.filter(worker_project_id__in=np_ids).delete()

        # 删除 WorkerProject 记录
        deleted_count = await WorkerProject.filter(id__in=np_ids).delete()

        logger.info(f"已清理{deleted_count}条过期记录, {files_deleted}条文件记录")

        return deleted_count

    async def record_project_files(self, worker_project_id, file_hashes):
        """记录文件清单"""
        await WorkerProjectFile.filter(worker_project_id=worker_project_id).delete()

        file_records = [
            WorkerProjectFile(
                worker_project_id=worker_project_id,
                file_path=path,
                file_hash=info["hash"],
                file_size=info["size"],
            )
            for path, info in file_hashes.items()
        ]

        if file_records:
            await WorkerProjectFile.bulk_create(file_records)
            logger.info(f"文件清单已记录 [{worker_project_id}] {len(file_records)}个文件")

    async def get_file_differences(self, worker_project_id, current_file_hashes):
        """计算文件差异"""
        worker_files = await WorkerProjectFile.filter(worker_project_id=worker_project_id).all()

        worker_file_map = {f.file_path: f.file_hash for f in worker_files}

        current_paths = set(current_file_hashes.keys())
        worker_paths = set(worker_file_map.keys())

        added = list(current_paths - worker_paths)
        deleted = list(worker_paths - current_paths)
        common = current_paths & worker_paths

        modified = [path for path in common if current_file_hashes[path] != worker_file_map[path]]
        unchanged = [path for path in common if current_file_hashes[path] == worker_file_map[path]]

        logger.info(f"文件差异 +{len(added)} ~{len(modified)} -{len(deleted)} ={len(unchanged)}")

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "unchanged": unchanged,
        }

    async def get_sync_statistics(self, worker_id=None):
        """统计同步状态"""
        query = WorkerProject.all()
        if worker_id:
            query = query.filter(worker_id=worker_id)

        all_records = await query.all()

        return {
            "total": len(all_records),
            "synced": len([r for r in all_records if r.status == "synced"]),
            "outdated": len([r for r in all_records if r.status == "outdated"]),
            "failed": len([r for r in all_records if r.status == "failed"]),
            "total_sync_count": sum(r.sync_count for r in all_records),
            "avg_sync_count": (
                sum(r.sync_count for r in all_records) / len(all_records) if all_records else 0
            ),
        }

worker_project_service = WorkerProjectService()
