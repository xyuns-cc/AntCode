"""Worker 服务层 - 管理分布式 Worker

核心 CRUD 操作，心跳/连接/统计功能已拆分到独立服务：
- worker_heartbeat_service.py: 心跳检测
- worker_connection_service.py: 连接管理
- worker_stats_service.py: 统计指标
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, status
from loguru import logger
from tortoise.expressions import Q
from tortoise.transactions import in_transaction

from antcode_core.application.services.workers.provisional_worker_cleanup import (
    mark_expired_provisional_worker_maintenance,
)
from antcode_core.application.services.workers.worker_connection_service import (
    worker_connection_service,
)
from antcode_core.application.services.workers.worker_delete_guard import (
    quiesce_worker_for_delete,
)
from antcode_core.application.services.workers.worker_heartbeat_service import worker_heartbeat_service
from antcode_core.application.services.workers.worker_identity_retirement import (
    clear_worker_credentials,
    persist_cleared_worker_credentials,
)
from antcode_core.application.services.workers.worker_lease_lifecycle import reconnectable_fence_reasons
from antcode_core.application.services.workers.worker_service_facade import WorkerServiceFacade
from antcode_core.application.services.workers.worker_service_lifecycle_wiring import (
    disable_worker_lease,
    enable_worker_lease,
)
from antcode_core.application.services.workers.worker_stats_service import worker_stats_service
from antcode_core.common.config import settings
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.models.enums import WorkerPermission
from antcode_core.domain.models.task_status_sets import TASK_RUN_ACTIVE_STATUSES

WorkerAclRevoker = Callable[[Worker], Awaitable[None]]
WorkerLeaseDisabler = Callable[[str, str], Awaitable[bool]]
WorkerLeaseEnabler = Callable[[str, tuple[str, ...]], Awaitable[bool]]


class WorkerService(WorkerServiceFacade):
    """Worker 服务类 - 核心 CRUD 操作"""

    HEARTBEAT_TIMEOUT = settings.WORKER_HEARTBEAT_TIMEOUT

    def __init__(
        self,
        acl_revoker: WorkerAclRevoker | None = None,
        *,
        lease_disabler: WorkerLeaseDisabler | None = None,
        lease_enabler: WorkerLeaseEnabler | None = None,
    ):
        """初始化 Worker 服务。"""
        self._heartbeat_service = worker_heartbeat_service
        self._connection_service = worker_connection_service
        self._stats_service = worker_stats_service
        self._acl_revoker = acl_revoker
        self._lease_disabler = lease_disabler
        self._lease_enabler = lease_enabler

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

    # 运行时委托由 WorkerServiceFacade 提供。

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

        worker = await Worker.create(
            name=request.name,
            host=host,
            port=port,
            region=request.region,
            description=request.description,
            tags=request.tags or [],
            status=WorkerStatus.OFFLINE.value,
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

        update_data = request.model_dump(exclude_unset=True)

        target_status = self._validated_update_status(update_data)
        previous_status = str(worker.status)

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

        if target_status in (WorkerStatus.OFFLINE.value, WorkerStatus.MAINTENANCE.value):
            await self._disable_worker_lease(worker, f"status:{target_status}")
        if target_status in (WorkerStatus.CONNECTING.value, WorkerStatus.ONLINE.value):
            await self._enable_worker_lease(worker, previous_status=previous_status)
        await worker.update_from_dict(update_data)
        await worker.save()

        logger.info(f"Worker 更新成功: {worker.name}")
        return worker

    @staticmethod
    def _validated_update_status(update_data: dict) -> str | None:
        if "status" not in update_data:
            return None
        try:
            normalized = WorkerStatus(str(update_data["status"]).strip().lower()).value
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Worker 状态无效") from exc
        update_data["status"] = normalized
        return normalized

    async def _retire_worker_identity(self, worker: Worker, reason: str) -> None:
        await self._disable_worker_lease(worker, reason)
        clear_worker_credentials(worker)

    async def _disable_worker_lease(self, worker: Worker, reason: str) -> bool:
        if self._lease_disabler is None:
            raise RuntimeError("Worker lease lifecycle disabler is not configured")
        return await self._lease_disabler(worker.public_id, reason)

    async def _enable_worker_lease(self, worker: Worker, *, previous_status: str) -> bool:
        if self._lease_enabler is None:
            raise RuntimeError("Worker lease lifecycle enabler is not configured")
        expected_reasons = reconnectable_fence_reasons(previous_status)
        return await self._lease_enabler(worker.public_id, expected_reasons)

    async def delete_worker(self, worker_id) -> bool:
        """删除 Worker（级联删除所有关联数据）

        P1-20: 级联删除 + Worker.delete() 走同一事务;任一步骤失败则整体回滚,
        Worker 不会残留在"半删"状态。
        P1-FN-06: 存在未终态执行时拒绝删除 —— 此前会先把这些 run 的
        worker_id 清空再删 Worker，在途结果因 ``_validate_result_source``
        要求归属 Worker 存在而被永久拒收。用户须先取消/等待这些 run。
        """
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return False

        await quiesce_worker_for_delete(worker)
        await self._retire_worker_identity(worker, "delete")
        await persist_cleared_worker_credentials(worker)
        await self._revoke_acl_before_delete(worker)
        # 级联删除所有关联数据（含 Worker 自身），失败即抛出让上层感知
        deleted_counts = await self._cascade_delete_worker_data(worker)

        logger.info(f"Worker 删除成功: {worker.name}, 级联删除: {deleted_counts}")
        return True

    async def delete_expired_provisional_worker(self, worker_id, run_settler) -> bool:
        """封禁身份、结算执行，再删除已过期且未确认的临时 Worker。"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return False
        await mark_expired_provisional_worker_maintenance(worker)
        await self._retire_worker_identity(worker, "registration-expired")
        await persist_cleared_worker_credentials(worker)
        await self._revoke_acl_before_delete(worker)
        await run_settler(worker.id)
        deleted_counts = await self._cascade_delete_worker_data(worker)
        logger.info("过期临时 Worker 删除成功: {}, 级联删除: {}", worker.name, deleted_counts)
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
            "task_executions_unbound": 0,
            "tasks_unbound": 0,
            "performance_history": 0,
            "spider_metrics": 0,
            "events": 0,
            "install_keys": 0,
            "projects_unbound": 0,
            "projects_runtime_unbound": 0,
            "projects_worker_id_unbound": 0,
        }

        try:
            async with in_transaction() as conn:
                # 复审 D3: 事务内锁 Worker 行并复查活跃 run —— 事务外
                # 预检与级联之间存在派发窗口（新 run 分配给该 worker 后
                # 被解绑 → 结果永久拒收）。复查失败整体回滚。
                locked = await Worker.filter(id=worker_internal_id).using_db(conn).select_for_update().first()
                if locked is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker 不存在")
                active = (
                    await TaskRun.filter(
                        worker_id=worker_internal_id,
                        status__in=list(TASK_RUN_ACTIVE_STATUSES),
                    )
                    .using_db(conn)
                    .count()
                )
                if active:
                    # 注意：此时 ACL 可能已撤销（tombstone-first 设计）。
                    # 拒绝删除保住执行归属；该 worker 需重新签发凭证。
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Worker {worker.name} 在删除期间新增 {active} 个未终态执行，已中止删除",
                    )

                # 1. 心跳记录
                deleted["heartbeats"] = await WorkerHeartbeat.filter(worker_id=worker_internal_id).delete()

                # 2. 用户 Worker 权限
                deleted["permissions"] = await UserWorkerPermission.filter(worker_id=worker_internal_id).delete()

                # 3. Worker 上的运行时及其绑定
                runtime_ids = await Runtime.filter(worker_id=worker_internal_id).values_list("id", flat=True)
                if runtime_ids:
                    runtime_ids = list(runtime_ids)
                    deleted["runtime_bindings"] = await ProjectRuntimeBinding.filter(
                        runtime_id__in=runtime_ids
                    ).delete()
                    deleted["runtimes"] = await Runtime.filter(id__in=runtime_ids).delete()

                # 4. 解绑项目上指向本 Worker 的字段(不删项目,项目归用户所有)
                deleted["projects_unbound"] = await Project.filter(bound_worker_id=worker_internal_id).update(
                    bound_worker_id=None
                )
                deleted["projects_runtime_unbound"] = await Project.filter(runtime_worker_id=worker_internal_id).update(
                    runtime_worker_id=None
                )
                # Project.worker_id 是 CharField(存 public_id)
                deleted["projects_worker_id_unbound"] = await Project.filter(worker_id=worker_public_id).update(
                    worker_id=None
                )

                # 5. Worker 删除只解绑调度偏好和执行归属，任务定义与历史执行
                # 是用户业务数据，不能随基础设施节点一起删除。
                deleted["tasks_unbound"] = await Task.filter(specified_worker_id=worker_internal_id).update(
                    specified_worker_id=None
                )
                deleted["task_executions_unbound"] = await TaskRun.filter(worker_id=worker_internal_id).update(
                    worker_id=None
                )

                # 6. 监控数据(worker_id 是字符串 public_id)
                deleted["performance_history"] = await WorkerPerformanceHistory.filter(
                    worker_id=worker_public_id
                ).delete()
                deleted["spider_metrics"] = await SpiderMetricsHistory.filter(worker_id=worker_public_id).delete()
                deleted["events"] = await WorkerEvent.filter(worker_id=worker_public_id).delete()

                # 7. 补漏:安装 Key 记录(used_by_worker 存 public_id)。
                # 复审 F4-A: status="expired" 的 Key 豁免删除 —— 它是过期
                # 注册清理的审计留存与补扫重试线索（acknowledge 依赖它精确
                # 报"恢复窗口已关闭"）。
                deleted["install_keys"] = (
                    await WorkerInstallKey.filter(used_by_worker=worker_public_id).exclude(status="expired").delete()
                )

                # 8. 最后删 Worker 本体,同事务保证原子
                await worker.delete()
        except Exception as e:
            logger.error(f"级联删除 Worker 数据失败(事务已回滚): {e}")
            raise

        return deleted

    async def _revoke_acl_before_delete(self, worker: Worker) -> None:
        if not settings.REDIS_ACL_ENABLED or not worker.redis_username:
            return
        if self._acl_revoker is None:
            raise RuntimeError("Redis ACL revoker 未配置，拒绝删除 Worker")
        setattr(worker, "redis_acl_synced_at", None)
        await worker.save(update_fields=["redis_acl_synced_at"])
        await self._acl_revoker(worker)

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

        workers_by_internal_id: dict[int, Worker] = {}
        if int_ids:
            workers_by_id = await Worker.filter(id__in=int_ids).all()
            workers_by_internal_id.update({worker.id: worker for worker in workers_by_id})
        if str_ids:
            workers_by_public_id = await Worker.filter(public_id__in=str_ids).all()
            workers_by_internal_id.update({worker.id: worker for worker in workers_by_public_id})
        workers_to_delete = list(workers_by_internal_id.values())

        if not workers_to_delete:
            return {
                "success_count": 0,
                "failed_count": len(worker_ids),
                "failed_ids": worker_ids,
            }

        # 逐个原子级联删除:仅统计成功的
        total_deleted: dict = {}
        succeeded_identifiers: set[str] = set()
        succeeded_worker_ids: set[int] = set()
        for worker in workers_to_delete:
            try:
                # P1-FN-06: 与单删同一保护，未终态执行的 Worker 保留并计入失败。
                await quiesce_worker_for_delete(worker)
                await self._retire_worker_identity(worker, "batch-delete")
                await persist_cleared_worker_credentials(worker)
                await self._revoke_acl_before_delete(worker)
                deleted = await self._cascade_delete_worker_data(worker)
                for key, count in deleted.items():
                    total_deleted[key] = total_deleted.get(key, 0) + count
                succeeded_identifiers.update((str(worker.id), worker.public_id))
                succeeded_worker_ids.add(worker.id)
            except Exception as e:
                # 事务已回滚,Worker 仍在;下次可重试
                logger.error(f"级联删除 Worker {worker.name} 数据失败,已回滚: {e}")

        success_count = len(succeeded_worker_ids)
        failed_ids = [worker_id for worker_id in worker_ids if str(worker_id) not in succeeded_identifiers]

        logger.info(f"批量删除 Worker: 成功{success_count}个, 级联删除: {total_deleted}")

        return {
            "success_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

    # ==================== 连接相关（委托） ====================

    async def disconnect_worker(self, worker_id) -> bool:
        """断开 Worker 连接"""
        worker = await self.get_worker_by_id(worker_id)
        if not worker:
            return False
        await self._disable_worker_lease(worker, "disconnect")
        return await self._connection_service.disconnect_worker(worker)

    # ==================== 心跳相关（委托） ====================

    async def heartbeat(
        self,
        worker_id,
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

    # ==================== Worker 权限管理 ====================

    async def get_user_workers(
        self,
        user_id: int,
        is_admin: bool = False,
        *,
        required_permission: str = "view",
    ):
        """获取用户可访问的 Worker 列表"""
        from antcode_core.domain.models import UserWorkerPermission

        if is_admin:
            return await Worker.all().order_by("-created_at")

        if required_permission == "view":
            permissions = await UserWorkerPermission.filter(
                user_id=user_id,
                permission__in=(WorkerPermission.VIEW.value, WorkerPermission.USE.value),
            ).all()
        elif required_permission == "use":
            permissions = await UserWorkerPermission.filter(
                user_id=user_id,
                permission=WorkerPermission.USE.value,
            ).all()
        else:
            raise ValueError("Worker 权限仅支持 view 或 use")
        worker_ids = [p.worker_id for p in permissions]

        if not worker_ids:
            return []

        return await Worker.filter(id__in=worker_ids).order_by("-created_at")

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
            return perm.permission in {WorkerPermission.VIEW.value, WorkerPermission.USE.value}
        if required_permission == "use":
            return perm.permission == WorkerPermission.USE.value

        return False


async def _revoke_worker_redis_access(worker: Worker) -> None:
    from antcode_core.common.security.redis_acl import revoke_worker_acl
    from antcode_core.infrastructure.redis import get_redis_client

    redis = await get_redis_client()
    if redis is None:
        raise RuntimeError("Redis client unavailable，无法撤销 Worker ACL")
    await revoke_worker_acl(redis, worker, clear_credentials=True)


worker_service = WorkerService(
    acl_revoker=_revoke_worker_redis_access,
    lease_disabler=disable_worker_lease,
    lease_enabler=enable_worker_lease,
)
