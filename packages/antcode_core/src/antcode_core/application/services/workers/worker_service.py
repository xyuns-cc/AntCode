"""Worker 服务层 - 管理分布式 Worker

核心 CRUD 操作，心跳/连接/统计功能已拆分到独立服务：
- worker_heartbeat_service.py: 心跳检测
- worker_connection_service.py: 连接管理
- worker_stats_service.py: 统计指标
"""

import secrets
from datetime import datetime

from fastapi import HTTPException, status
from loguru import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from antcode_core.application.services.workers.worker_connection_service import (
    worker_connection_service,
)

# 导入拆分的服务
from antcode_core.application.services.workers.worker_heartbeat_service import worker_heartbeat_service
from antcode_core.application.services.workers.worker_stats_service import worker_stats_service
from antcode_core.common.config import settings
from antcode_core.domain.models import Worker, WorkerStatus


class WorkerService:
    """Worker 服务类 - 核心 CRUD 操作"""

    # 心跳超时时间（秒）
    HEARTBEAT_TIMEOUT = settings.WORKER_HEARTBEAT_TIMEOUT

    def __init__(self):
        """初始化 Worker 服务"""
        # 委托给心跳服务
        self._heartbeat_service = worker_heartbeat_service
        self._connection_service = worker_connection_service
        self._stats_service = worker_stats_service

    @staticmethod
    def _normalize_status_filter(status_filter: WorkerStatus | str | None) -> str | None:
        if status_filter is None:
            return None
        if isinstance(status_filter, WorkerStatus):
            return status_filter.value
        if isinstance(status_filter, str):
            value = status_filter.strip().lower()
            if not value:
                return None
            try:
                return WorkerStatus(value).value
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="无效的状态过滤值") from exc
        return None

    # ==================== 委托方法 ====================

    async def init_heartbeat_cache(self):
        """初始化心跳检测缓存"""
        await self._heartbeat_service.init_heartbeat_cache()

    async def refresh_worker_cache(self):
        """刷新 Worker 缓存"""
        await self._heartbeat_service.refresh_worker_cache()

    async def init_worker_secrets(self):
        """初始化 Worker 密钥"""
        await self._connection_service.init_worker_secrets()

    async def register_direct_worker(self, request):
        """Direct Worker 注册（使用 worker_id 作为 public_id）"""
        return await self._connection_service.register_direct_worker(request)

    async def smart_health_check(self):
        """智能心跳检测"""
        return await self._heartbeat_service.smart_health_check()

    async def check_all_workers_health(self):
        """检查所有 Worker 健康状态"""
        return await self._heartbeat_service.check_all_workers_health()

    async def manual_test_worker(self, worker_id: int) -> bool:
        """手动测试 Worker"""
        return await self._heartbeat_service.manual_test_worker(worker_id)

    async def get_aggregate_stats(self):
        """获取聚合统计"""
        return await self._stats_service.get_aggregate_stats()

    async def get_metrics_history(self, worker_id: int, hours: int = 24):
        """获取历史指标"""
        return await self._stats_service.get_metrics_history(worker_id, hours)

    async def get_cluster_metrics_history(self, hours: int = 24):
        """获取集群历史指标"""
        return await self._stats_service.get_cluster_metrics_history(hours)

    # ==================== 核心 CRUD 操作 ====================

    async def get_workers(
        self,
        page: int = 1,
        size: int = 20,
        status_filter: WorkerStatus | str | None = None,
        region: str | None = None,
        search: str | None = None,
    ):
        """获取 Worker 列表"""
        query = Worker.all()

        status_value = self._normalize_status_filter(status_filter)
        if status_value == WorkerStatus.ONLINE.value:
            try:
                await self._heartbeat_service.smart_health_check()
            except Exception as e:
                logger.debug(f"刷新 Worker 心跳状态失败: {e}")
        if status_value:
            query = query.filter(status=status_value)

        if region:
            query = query.filter(region=region)

        if search:
            query = query.filter(
                Q(name__icontains=search) | Q(host__icontains=search) | Q(description__icontains=search)
            )

        total = await query.count()
        offset = (page - 1) * size
        workers = await query.order_by("-created_at").offset(offset).limit(size)

        # 检查并更新离线 Worker 状态
        await self._heartbeat_service.check_offline_workers(workers)

        return workers, total

    async def get_all_workers(self):
        """获取所有 Worker（不分页）"""
        workers = await Worker.all().order_by("-created_at")
        await self._heartbeat_service.check_offline_workers(workers)
        return workers

    async def get_worker_by_id(self, worker_id) -> Worker | None:
        """根据ID获取 Worker"""
        # 尝试作为 public_id
        worker = await Worker.filter(public_id=str(worker_id)).first()
        if worker:
            return worker

        # 尝试作为内部 ID
        try:
            internal_id = int(worker_id)
            return await Worker.filter(id=internal_id).first()
        except (ValueError, TypeError):
            return None

    async def get_worker_by_public_id(self, public_id: str) -> Worker | None:
        """根据 public_id 获取 Worker"""
        return await Worker.filter(public_id=public_id).first()

    async def create_worker(self, request, user_id: int | None = None) -> Worker:
        """创建 Worker"""
        # 检查名称是否已存在
        existing = await Worker.filter(name=request.name).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Worker 名称已存在")

        host, port = self._connection_service.normalize_address(request.host, request.port)

        # 检查地址是否已存在
        existing = await Worker.filter(host=host, port=port).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该地址已被其他 Worker 使用")

        # 生成 API 密钥
        api_key = secrets.token_hex(32)
        secret_key = secrets.token_hex(64)

        worker = await Worker.create(
            name=request.name,
            host=host,
            port=port,
            region=request.region,
            description=request.description,
            tags=request.tags or [],
            status=WorkerStatus.OFFLINE.value,
            api_key=api_key,
            secret_key=secret_key,
            created_by=user_id,
            transport_mode="gateway",
        )

        logger.info(f"Worker 创建成功: {worker.name} ({worker.host}:{worker.port})")
        return worker

    async def update_worker(self, worker_id, request) -> Worker | None:
        """更新 Worker"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return None

        update_data = request.dict(exclude_unset=True)

        # 检查名称唯一性
        if "name" in update_data and update_data["name"] != worker.name:
            existing = await Worker.filter(name=update_data["name"]).first()
            if existing:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Worker 名称已存在")

        # 检查地址唯一性
        new_host = update_data.get("host", worker.host)
        new_port = update_data.get("port", worker.port)
        if "host" in update_data or "port" in update_data:
            new_host, new_port = self._connection_service.normalize_address(new_host, new_port)
            update_data["host"] = new_host
            update_data["port"] = new_port
        if new_host != worker.host or new_port != worker.port:
            existing = await Worker.filter(host=new_host, port=new_port).exclude(id=worker.id).first()
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该地址已被其他 Worker 使用",
                )

        await worker.update_from_dict(update_data)
        await worker.save()

        logger.info(f"Worker 更新成功: {worker.name}")
        return worker

    async def delete_worker(self, worker_id) -> bool:
        """删除 Worker（级联删除所有关联数据）

        P1-20: 级联删除 + Worker.delete() 走同一事务;任一步骤失败则整体回滚,
        Worker 不会残留在"半删"状态。
        """
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return False

        # 级联删除所有关联数据（含 Worker 自身），失败即抛出让上层感知
        deleted_counts = await self._cascade_delete_worker_data(worker)

        logger.info(f"Worker 删除成功: {worker.name}, 级联删除: {deleted_counts}")
        return True

    async def _cascade_delete_worker_data(self, worker: Worker) -> dict:
        """级联删除 Worker 的所有关联数据（含 Worker 自身），单事务原子。

        P1-20 修复要点:
        - 全部子表操作 + Worker 自身 delete 放在同一 `in_transaction()` 内,
          任一步骤异常会回滚,不会出现 "子表半删 / Worker 残留" 的状态。
        - 补齐此前遗漏的关联字段:
          * Project.bound_worker_id / runtime_worker_id (BigInt) → 解绑
          * Project.worker_id (CharField, 存 public_id) → 解绑
          * WorkerInstallKey.used_by_worker (public_id) → 删除历史 Key
        - 未加真实 FK 之前,这些字段是"逻辑外键";
          解绑而非删项目/任务,因为它们归用户所有,不该跟着 Worker 一起没。
        """
        from antcode_core.domain.models import (
            Project,
            ProjectRuntimeBinding,
            Runtime,
            UserWorkerPermission,
            WorkerHeartbeat,
            WorkerInstallKey,
        )
        from antcode_core.domain.models.monitoring import (
            SpiderMetricsHistory,
            WorkerEvent,
            WorkerPerformanceHistory,
        )
        from antcode_core.domain.models.task import Task
        from antcode_core.domain.models.task_run import TaskRun

        worker_internal_id = worker.id
        worker_public_id = worker.public_id

        deleted = {
            "heartbeats": 0,
            "permissions": 0,
            "runtimes": 0,
            "runtime_bindings": 0,
            "task_executions": 0,
            "tasks": 0,
            "performance_history": 0,
            "spider_metrics": 0,
            "events": 0,
            "install_keys": 0,
            "projects_unbound": 0,
            "projects_runtime_unbound": 0,
            "projects_worker_id_unbound": 0,
        }

        try:
            async with in_transaction():
                # 1. 心跳记录
                deleted["heartbeats"] = await WorkerHeartbeat.filter(
                    worker_id=worker_internal_id
                ).delete()

                # 2. 用户 Worker 权限
                deleted["permissions"] = await UserWorkerPermission.filter(
                    worker_id=worker_internal_id
                ).delete()

                # 3. Worker 上的运行时及其绑定
                runtime_ids = await Runtime.filter(
                    worker_id=worker_internal_id
                ).values_list("id", flat=True)
                if runtime_ids:
                    runtime_ids = list(runtime_ids)
                    deleted["runtime_bindings"] = await ProjectRuntimeBinding.filter(
                        runtime_id__in=runtime_ids
                    ).delete()
                    deleted["runtimes"] = await Runtime.filter(
                        id__in=runtime_ids
                    ).delete()

                # 4. 解绑项目上指向本 Worker 的字段(不删项目,项目归用户所有)
                deleted["projects_unbound"] = await Project.filter(
                    bound_worker_id=worker_internal_id
                ).update(bound_worker_id=None)
                deleted["projects_runtime_unbound"] = await Project.filter(
                    runtime_worker_id=worker_internal_id
                ).update(runtime_worker_id=None)
                # Project.worker_id 是 CharField(存 public_id)
                deleted["projects_worker_id_unbound"] = await Project.filter(
                    worker_id=worker_public_id
                ).update(worker_id=None)

                # 5. Worker 上的任务及执行记录(specified_worker 语义是"绑死这台 Worker"
                #    的任务,Worker 没了它们无处执行,与运行时绑定一起删是历史行为)
                task_ids = await Task.filter(
                    specified_worker_id=worker_internal_id
                ).values_list("id", flat=True)
                if task_ids:
                    task_ids = list(task_ids)
                    deleted["task_executions"] = await TaskRun.filter(
                        task_id__in=task_ids
                    ).delete()
                    deleted["tasks"] = await Task.filter(
                        id__in=task_ids
                    ).delete()

                # 5b. 其它历史 TaskRun.worker_id 指向本 Worker 的记录:
                #     属于其它任务的执行痕迹,置空以保留任务历史(避免主键悬挂)。
                await TaskRun.filter(worker_id=worker_internal_id).update(worker_id=None)

                # 6. 监控数据(worker_id 是字符串 public_id)
                deleted["performance_history"] = await WorkerPerformanceHistory.filter(
                    worker_id=worker_public_id
                ).delete()
                deleted["spider_metrics"] = await SpiderMetricsHistory.filter(
                    worker_id=worker_public_id
                ).delete()
                deleted["events"] = await WorkerEvent.filter(
                    worker_id=worker_public_id
                ).delete()

                # 7. 补漏:安装 Key 记录(used_by_worker 存 public_id)
                deleted["install_keys"] = await WorkerInstallKey.filter(
                    used_by_worker=worker_public_id
                ).delete()

                # 8. 最后删 Worker 本体,同事务保证原子
                await worker.delete()
        except Exception as e:
            logger.error(f"级联删除 Worker 数据失败(事务已回滚): {e}")
            raise

        return deleted

    async def batch_delete_workers(self, worker_ids: list) -> dict:
        """批量删除 Worker（级联删除所有关联数据）

        P1-20: 每个 Worker 单独走一次原子事务;若级联失败,该 Worker 会被
        保留(而非旧实现里"子表清了、Worker 却也一并删了"的坏状态)。
        """
        # 批量解析 Worker ID（支持 public_id 和内部 ID 混合），避免 N+1 查询
        int_ids = []
        str_ids = []
        for worker_id in worker_ids:
            if isinstance(worker_id, int) or (isinstance(worker_id, str) and worker_id.isdigit()):
                int_ids.append(int(worker_id) if isinstance(worker_id, str) else worker_id)
            else:
                str_ids.append(worker_id)

        workers_to_delete = []
        if int_ids:
            workers_by_id = await Worker.filter(id__in=int_ids).all()
            workers_to_delete.extend(workers_by_id)
        if str_ids:
            workers_by_public_id = await Worker.filter(public_id__in=str_ids).all()
            workers_to_delete.extend(workers_by_public_id)

        if not workers_to_delete:
            return {
                "success_count": 0,
                "failed_count": len(worker_ids),
                "failed_ids": worker_ids,
            }

        # 逐个原子级联删除:仅统计成功的
        total_deleted: dict = {}
        succeeded_public_ids: list[str] = []
        for worker in workers_to_delete:
            try:
                deleted = await self._cascade_delete_worker_data(worker)
                for key, count in deleted.items():
                    total_deleted[key] = total_deleted.get(key, 0) + count
                succeeded_public_ids.append(worker.public_id)
            except Exception as e:
                # 事务已回滚,Worker 仍在;下次可重试
                logger.error(f"级联删除 Worker {worker.name} 数据失败,已回滚: {e}")

        success_count = len(succeeded_public_ids)
        requested = set(str(x) for x in worker_ids)
        succeeded_set = set(succeeded_public_ids)
        failed_ids = [x for x in worker_ids if str(x) not in succeeded_set and str(x) in requested]

        logger.info(f"批量删除 Worker: 成功{success_count}个, 级联删除: {total_deleted}")

        return {
            "success_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

    # ==================== 连接相关（委托） ====================

    async def register_worker(self, request):
        """Worker 自注册"""
        return await self._connection_service.register_worker(request)

    async def disconnect_worker(self, worker_id) -> bool:
        """断开 Worker 连接"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return False
        return await self._connection_service.disconnect_worker(worker)

    async def test_connection(self, worker_id):
        """测试 Worker 连接"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return {"success": False, "error": "Worker 不存在"}
        return await self._connection_service.test_connection(worker)

    async def refresh_worker_status(self, worker_id):
        """刷新 Worker 状态"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return None
        return await self._connection_service.refresh_worker_status(worker)

    # ==================== 心跳相关（委托） ====================

    async def heartbeat(
        self,
        worker_id,
        api_key: str,
        status_value=None,
        metrics=None,
        version: str | None = None,
        os_type: str | None = None,
        os_version: str | None = None,
        python_version: str | None = None,
        machine_arch: str | None = None,
        capabilities: dict | None = None,
        spider_stats: dict | None = None,
    ) -> bool:
        """处理 Worker 心跳"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            logger.warning(f"心跳失败: Worker 不存在 {worker_id}")
            return False

        # 验证 API 密钥
        if worker.api_key != api_key:
            logger.warning(f"心跳失败: API密钥不匹配 {worker_id}")
            return False

        # 委托给心跳服务
        metrics_dict = metrics.model_dump(exclude_none=True) if metrics else None
        return await self._heartbeat_service.heartbeat(
            worker=worker,
            status_value=status_value,
            metrics=metrics_dict,
            version=version,
            os_type=os_type,
            os_version=os_version,
            python_version=python_version,
            machine_arch=machine_arch,
            capabilities=capabilities,
            spider_stats=spider_stats,
        )

    async def verify_api_key(self, worker: Worker, api_key: str) -> bool:
        """验证 Worker 的 API Key"""
        if not worker or not api_key:
            return False
        return worker.api_key == api_key

    # ==================== Worker 权限管理 ====================

    async def get_user_workers(self, user_id: int, is_admin: bool = False):
        """获取用户可访问的 Worker 列表"""
        from antcode_core.domain.models import UserWorkerPermission

        if is_admin:
            return await Worker.all().order_by("-created_at")

        permissions = await UserWorkerPermission.filter(user_id=user_id).all()
        worker_ids = [p.worker_id for p in permissions]

        if not worker_ids:
            return []

        return await Worker.filter(id__in=worker_ids).order_by("-created_at")

    async def assign_worker_to_user(
        self,
        worker_id: int,
        user_id: int,
        permission: str = "use",
        assigned_by: int | None = None,
        note: str | None = None,
    ) -> bool:
        """给用户分配 Worker 权限"""
        from antcode_core.domain.models import User, UserWorkerPermission

        # 检查用户是否是管理员
        user = await User.filter(id=user_id).first()
        if user and user.is_admin:
            raise HTTPException(status_code=400, detail="管理员默认拥有全部 Worker 权限，无需分配")

        # 检查 Worker 是否存在
        worker = await Worker.filter(id=worker_id).first()
        if not worker:
            raise HTTPException(status_code=404, detail="Worker 不存在")

        # 检查是否已有权限
        existing = await UserWorkerPermission.filter(user_id=user_id, worker_id=worker_id).first()

        if existing:
            existing.permission = permission
            existing.assigned_by = assigned_by
            existing.note = note
            await existing.save()
            logger.info(f"更新用户 {user_id} 的 Worker {worker.name} 权限: {permission}")
        else:
            await UserWorkerPermission.create(
                user_id=user_id,
                worker_id=worker_id,
                permission=permission,
                assigned_by=assigned_by,
                note=note,
            )
            logger.info(f"分配 Worker {worker.name} 给用户 {user_id}, 权限: {permission}")

        return True

    async def revoke_worker_from_user(self, worker_id: int, user_id: int) -> bool:
        """撤销用户的 Worker 权限"""
        from antcode_core.domain.models import UserWorkerPermission

        deleted = await UserWorkerPermission.filter(user_id=user_id, worker_id=worker_id).delete()

        if deleted:
            logger.info(f"撤销用户 {user_id} 的 Worker {worker_id} 权限")

        return deleted > 0

    async def get_worker_users(self, worker_id: int) -> list[dict]:
        """获取 Worker 的授权用户列表"""
        from antcode_core.domain.models import User, UserWorkerPermission

        permissions = await UserWorkerPermission.filter(worker_id=worker_id).all()

        if not permissions:
            return []

        user_ids = [perm.user_id for perm in permissions]
        users = await User.filter(id__in=user_ids, is_admin=False).all()
        user_map = {u.id: u for u in users}

        result = []
        for perm in permissions:
            user = user_map.get(perm.user_id)
            if user:
                result.append(
                    {
                        "user_id": user.public_id,
                        "username": user.username,
                        "permission": perm.permission,
                        "assigned_at": perm.assigned_at.isoformat() if perm.assigned_at else None,
                        "note": perm.note,
                    }
                )

        return result

    async def get_user_worker_permissions(self, user_id: int) -> list[dict]:
        """获取用户的所有 Worker 权限"""
        from antcode_core.domain.models import UserWorkerPermission

        permissions = await UserWorkerPermission.filter(user_id=user_id).all()

        if not permissions:
            return []

        # 批量查询所有关联的 Worker，避免 N+1 查询
        worker_ids = [perm.worker_id for perm in permissions]
        workers = await Worker.filter(id__in=worker_ids).all()
        worker_map = {w.id: w for w in workers}

        result = []
        for perm in permissions:
            worker = worker_map.get(perm.worker_id)
            if worker:
                result.append(
                    {
                        "worker_id": worker.id,
                        "worker_name": worker.name,
                        "worker_host": worker.host,
                        "worker_port": worker.port,
                        "worker_status": worker.status,
                        "permission": perm.permission,
                        "assigned_at": perm.assigned_at.isoformat() if perm.assigned_at else None,
                        "note": perm.note,
                    }
                )

        return result

    async def check_user_worker_permission(
        self,
        user_id: int,
        worker_id: int,
        is_admin: bool = False,
        required_permission: str = "use",
    ) -> bool:
        """检查用户是否有 Worker 权限"""
        from antcode_core.domain.models import UserWorkerPermission

        if is_admin:
            return True

        perm = await UserWorkerPermission.filter(user_id=user_id, worker_id=worker_id).first()

        if not perm:
            return False

        if required_permission == "view":
            return perm.permission in ["view", "use"]
        elif required_permission == "use":
            return perm.permission == "use"

        return False

    async def batch_assign_workers(
        self,
        user_id: int,
        worker_ids: list[int],
        permission: str = "use",
        assigned_by: int | None = None,
    ) -> dict:
        """批量分配 Worker 权限给用户"""
        from antcode_core.domain.models import UserWorkerPermission

        existing_perms = await UserWorkerPermission.filter(user_id=user_id, worker_id__in=worker_ids).values_list(
            "worker_id", flat=True
        )

        existing_worker_ids = set(existing_perms)

        new_permissions = []
        for worker_id in worker_ids:
            if worker_id not in existing_worker_ids:
                new_permissions.append(
                    UserWorkerPermission(
                        user_id=user_id,
                        worker_id=worker_id,
                        permission=permission,
                        assigned_by=assigned_by,
                        assigned_at=datetime.now(),
                    )
                )

        if new_permissions:
            await UserWorkerPermission.bulk_create(new_permissions)

        logger.info(f"批量分配 Worker 权限: 用户{user_id}, 新增{len(new_permissions)}个")

        return {
            "success": len(new_permissions),
            "failed": 0,
            "skipped": len(existing_worker_ids),
        }

    async def batch_revoke_workers(self, user_id: int, worker_ids: list[int]) -> dict:
        """批量撤销用户的 Worker 权限"""
        from antcode_core.domain.models import UserWorkerPermission

        deleted = await UserWorkerPermission.filter(user_id=user_id, worker_id__in=worker_ids).delete()

        return {"revoked": deleted}


# 创建服务实例
worker_service = WorkerService()
