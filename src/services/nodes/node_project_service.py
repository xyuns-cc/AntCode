"""节点项目分发服务 - 管理分布式同步状态"""

from typing import Optional, Dict, Any, List
from datetime import datetime

from loguru import logger
from tortoise.expressions import Q

from src.models import NodeProject, NodeProjectFile


class NodeProjectService:
    """节点项目分发服务"""

    async def check_node_has_project(
        self, 
        node_id: int, 
        project_public_id: str
    ) -> Optional[NodeProject]:
        """检查节点项目状态"""
        return await NodeProject.get_or_none(
            node_id=node_id,
            project_public_id=project_public_id
        )

    async def is_project_outdated(
        self, 
        node_id: int, 
        project_public_id: str,
        current_hash: str
    ) -> bool:
        """验证项目版本是否过期"""
        node_project = await self.check_node_has_project(node_id, project_public_id)

        if not node_project:
            return True

        if node_project.file_hash != current_hash:
            logger.info(
                f"项目版本过期 [{project_public_id}@节点{node_id}] "
                f"本地:{node_project.file_hash[:8]} 最新:{current_hash[:8]}"
            )
            return True

        return False

    async def record_project_sync(
        self,
        node_id: int,
        project_id: int,
        project_public_id: str,
        file_hash: str,
        file_size: int,
        transfer_method: str,
        node_local_project_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> NodeProject:
        """记录同步状态"""
        node_project = await NodeProject.get_or_none(
            node_id=node_id,
            project_public_id=project_public_id
        )

        if node_project:
            node_project.file_hash = file_hash
            node_project.file_size = file_size
            node_project.transfer_method = transfer_method
            node_project.status = "synced"
            node_project.sync_count += 1
            if node_local_project_id:
                node_project.node_local_project_id = node_local_project_id
            if metadata:
                node_project.metadata = metadata
            await node_project.save()

            logger.info(
                f"同步记录已更新 [节点{node_id}@{project_public_id}] "
                f"同步次数:{node_project.sync_count}"
            )
        else:
            node_project = await NodeProject.create(
                node_id=node_id,
                project_id=project_id,
                project_public_id=project_public_id,
                node_local_project_id=node_local_project_id,
                file_hash=file_hash,
                file_size=file_size,
                transfer_method=transfer_method,
                status="synced",
                metadata=metadata,
            )

            logger.info(f"同步记录已创建 [节点{node_id}@{project_public_id}]")

        return node_project

    async def mark_project_used(
        self,
        node_id: int,
        project_public_id: str
    ):
        """更新项目使用时间"""
        node_project = await self.check_node_has_project(node_id, project_public_id)
        if node_project:
            node_project.last_used_at = datetime.now()
            await node_project.save()

    async def mark_project_outdated(
        self,
        project_public_id: str
    ):
        """标记项目过期"""
        await NodeProject.filter(
            project_public_id=project_public_id
        ).update(status="outdated")

        logger.info(f"项目已标记过期 [{project_public_id}]")

    async def get_node_projects(
        self,
        node_id: int,
        status: Optional[str] = None
    ) -> List[NodeProject]:
        """查询节点项目列表"""
        query = NodeProject.filter(node_id=node_id)
        if status:
            query = query.filter(status=status)
        return await query.all()

    async def get_project_nodes(
        self,
        project_public_id: str,
        status: Optional[str] = None
    ) -> List[NodeProject]:
        """查询项目分发节点"""
        query = NodeProject.filter(project_public_id=project_public_id)
        if status:
            query = query.filter(status=status)
        return await query.all()

    async def delete_node_project(
        self,
        node_id: int,
        project_public_id: str
    ) -> bool:
        """删除同步记录（级联删除文件记录）"""
        # 先获取 NodeProject 记录
        node_project = await NodeProject.filter(
            node_id=node_id,
            project_public_id=project_public_id
        ).first()

        if not node_project:
            return False

        # 删除关联的文件记录
        await NodeProjectFile.filter(node_project_id=node_project.id).delete()

        # 删除 NodeProject 记录
        await node_project.delete()

        return True

    async def cleanup_outdated_records(
        self,
        days: int = 30
    ) -> int:
        """清理过期记录（级联删除文件记录）"""
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

        # 先获取要删除的 NodeProject 记录
        outdated_projects = await NodeProject.filter(
            Q(status="outdated") & 
            (Q(last_used_at__lt=cutoff_date) | Q(last_used_at__isnull=True))
        ).all()

        if not outdated_projects:
            return 0

        # 删除关联的文件记录
        np_ids = [np.id for np in outdated_projects]
        files_deleted = await NodeProjectFile.filter(node_project_id__in=np_ids).delete()

        # 删除 NodeProject 记录
        deleted_count = await NodeProject.filter(id__in=np_ids).delete()

        logger.info(f"已清理{deleted_count}条过期记录, {files_deleted}条文件记录")

        return deleted_count

    async def record_project_files(
        self,
        node_project_id: int,
        file_hashes: Dict[str, Dict[str, Any]]
    ):
        """记录文件清单"""
        await NodeProjectFile.filter(node_project_id=node_project_id).delete()

        file_records = [
            NodeProjectFile(
                node_project_id=node_project_id,
                file_path=path,
                file_hash=info["hash"],
                file_size=info["size"]
            )
            for path, info in file_hashes.items()
        ]

        if file_records:
            await NodeProjectFile.bulk_create(file_records)
            logger.info(f"文件清单已记录 [{node_project_id}] {len(file_records)}个文件")

    async def get_file_differences(
        self,
        node_project_id: int,
        current_file_hashes: Dict[str, str]
    ) -> Dict[str, List[str]]:
        """计算文件差异"""
        node_files = await NodeProjectFile.filter(
            node_project_id=node_project_id
        ).all()

        node_file_map = {f.file_path: f.file_hash for f in node_files}

        current_paths = set(current_file_hashes.keys())
        node_paths = set(node_file_map.keys())

        added = list(current_paths - node_paths)
        deleted = list(node_paths - current_paths)
        common = current_paths & node_paths

        modified = [
            path for path in common
            if current_file_hashes[path] != node_file_map[path]
        ]
        unchanged = [
            path for path in common
            if current_file_hashes[path] == node_file_map[path]
        ]

        logger.info(
            f"文件差异 +{len(added)} ~{len(modified)} -{len(deleted)} ={len(unchanged)}"
        )

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "unchanged": unchanged
        }

    async def get_sync_statistics(self, node_id: Optional[int] = None) -> Dict:
        """统计同步状态"""
        query = NodeProject.all()
        if node_id:
            query = query.filter(node_id=node_id)

        all_records = await query.all()

        return {
            "total": len(all_records),
            "synced": len([r for r in all_records if r.status == "synced"]),
            "outdated": len([r for r in all_records if r.status == "outdated"]),
            "failed": len([r for r in all_records if r.status == "failed"]),
            "total_sync_count": sum(r.sync_count for r in all_records),
            "avg_sync_count": (
                sum(r.sync_count for r in all_records) / len(all_records)
                if all_records else 0
            ),
        }


node_project_service = NodeProjectService()

