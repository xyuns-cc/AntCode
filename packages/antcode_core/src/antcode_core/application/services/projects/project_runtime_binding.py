"""Worker runtime selection and binding for project creation."""

from fastapi import HTTPException, status
from loguru import logger

from antcode_core.application.services.projects.runtime_rollback import (
    RuntimeReservation,
    RuntimeRollback,
)
from antcode_core.application.services.runtime.runtime_control_service import (
    RuntimeControlService,
    runtime_control_service,
)
from antcode_core.application.services.workers import worker_service
from antcode_core.domain.models.enums import RuntimeKind, RuntimeScope


class ProjectRuntimeBindingMixin:
    """Binds a project to a validated existing or newly created Worker runtime."""

    def __init__(self, runtime_service: RuntimeControlService = runtime_control_service) -> None:
        self._runtime_service = runtime_service

    async def _setup_project_environment(
        self,
        project,
        request,
        *,
        user_id,
        conn,
        rollback: RuntimeRollback | None = None,
    ) -> None:
        if not getattr(request, "runtime_scope", None):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须选择运行时作用域")
        await self._setup_worker_environment(
            project,
            request,
            user_id=user_id,
            conn=conn,
            rollback=rollback,
        )

    async def _setup_worker_environment(
        self,
        project,
        request,
        *,
        user_id,
        conn,
        rollback: RuntimeRollback | None = None,
    ) -> None:
        worker, created_by, is_admin = await self._authorize_worker(request, user_id)
        runtime = self._worker_runtime_request(request)
        self._require_dependency_install_permission(runtime, is_admin)
        runtime.update({"created_by": created_by, "owner_user_id": str(user_id), "is_admin": is_admin})
        await self._resolve_worker_environment(project, runtime, rollback=rollback)
        await self._bind_worker_runtime(project, worker, runtime, conn=conn)

    @staticmethod
    async def _authorize_worker(request, user_id):
        from antcode_core.application.services.users.user_service import user_service
        from antcode_core.domain.models.worker import Worker

        worker_id = getattr(request, "worker_id", None)
        if not worker_id:
            raise HTTPException(status_code=400, detail="使用 Worker 运行时必须指定 worker_id")
        worker = await Worker.get_or_none(public_id=worker_id)
        if not worker:
            raise HTTPException(status_code=404, detail="Worker 不存在")
        if worker.status != "online":
            raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")
        user_obj = await user_service.get_user_by_id(user_id)
        if user_obj and user_obj.is_admin:
            return worker, user_obj.username, True
        allowed = await worker_service.check_user_worker_permission(
            user_id=user_id,
            worker_id=worker.id,
            is_admin=False,
            required_permission="use",
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="无 Worker 访问权限")
        return worker, user_obj.username if user_obj else str(user_id), False

    @staticmethod
    def _require_dependency_install_permission(runtime, is_admin) -> None:
        if runtime["use_existing"] or not runtime["dependencies"] or is_admin:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可在项目创建时安装 Worker 依赖；普通用户必须使用无依赖的新环境或现有环境",
        )

    @staticmethod
    def _worker_runtime_request(request):
        # RuntimeKind 只有 python 一个成员，非法值在 pydantic 边界就被拒，这里不再复查。
        runtime_kind = getattr(request, "runtime_kind", RuntimeKind.PYTHON)
        dependencies = getattr(request, "dependencies", None)
        return {
            "worker_id": request.worker_id,
            "use_existing": getattr(request, "use_existing_env", False),
            "scope": request.runtime_scope,
            "kind": runtime_kind,
            "python_version": getattr(request, "python_version", None),
            "existing_name": getattr(request, "existing_env_name", None),
            "shared_key": getattr(request, "shared_runtime_key", None),
            "requested_name": getattr(request, "env_name", None),
            "dependencies": dependencies if isinstance(dependencies, list) else [],
        }

    async def _resolve_worker_environment(self, project, runtime, *, rollback) -> None:
        if runtime["use_existing"]:
            await self._select_existing_environment(runtime, self._runtime_service)
            return
        await self._create_worker_environment(
            project,
            runtime,
            self._runtime_service,
            rollback=rollback,
        )

    @staticmethod
    async def _select_existing_environment(runtime, runtime_service) -> None:
        env_name = runtime["existing_name"]
        if not env_name:
            raise HTTPException(status_code=400, detail="使用现有环境时必须指定 existing_env_name")
        result = await runtime_service.get_env(runtime["worker_id"], env_name)
        if not result.get("success") or not result.get("data"):
            raise HTTPException(status_code=400, detail="指定环境不存在或不可用")
        env = result["data"]
        ProjectRuntimeBindingMixin._validate_existing_environment(runtime, env)
        runtime["env_name"] = env_name
        runtime["python_version"] = runtime["python_version"] or env.get("python_version")

    @staticmethod
    def _validate_existing_environment(runtime, env) -> None:
        env_scope = env.get("scope")
        if env_scope not in {RuntimeScope.SHARED.value, RuntimeScope.PRIVATE.value}:
            raise HTTPException(status_code=500, detail="Worker 返回的运行时作用域无效")
        if env_scope == RuntimeScope.PRIVATE.value and not runtime["is_admin"]:
            if str(env.get("owner_user_id")) != runtime["owner_user_id"]:
                raise HTTPException(status_code=403, detail="无权绑定其他用户的私有运行时环境")
        if runtime["scope"] == RuntimeScope.SHARED and env_scope != RuntimeScope.SHARED.value:
            raise HTTPException(status_code=400, detail="共享环境必须选择共享运行时")
        if runtime["scope"] == RuntimeScope.PRIVATE and env_scope != RuntimeScope.PRIVATE.value:
            raise HTTPException(status_code=400, detail="私有环境不允许使用共享运行时")

    @staticmethod
    async def _create_worker_environment(
        project,
        runtime,
        runtime_service,
        *,
        rollback: RuntimeRollback | None = None,
    ) -> None:
        ProjectRuntimeBindingMixin._prepare_new_runtime(project, runtime)
        result = await runtime_service.create_env(
            worker_id=runtime["worker_id"],
            env_name=runtime["env_name"],
            python_version=runtime["python_version"],
            packages=runtime["dependencies"],
            created_by=runtime["created_by"],
            owner_user_id=runtime["owner_user_id"],
        )
        if not result.get("success"):
            detail = result.get("error") or "未知错误"
            raise HTTPException(status_code=500, detail=f"创建运行时失败: {detail}")
        if rollback is not None:
            rollback.register(RuntimeReservation(runtime["worker_id"], runtime["env_name"]))

    @staticmethod
    def _prepare_new_runtime(project, runtime) -> None:
        if runtime["scope"] == RuntimeScope.SHARED:
            raise HTTPException(status_code=400, detail="共享环境必须从环境管理中选择现有环境")
        python_version = runtime["python_version"]
        if not python_version:
            raise HTTPException(status_code=400, detail="创建新环境时必须指定 python_version")
        requested_name = runtime["requested_name"]
        if requested_name and requested_name.startswith("shared-"):
            raise HTTPException(status_code=400, detail="私有环境名称不允许以 shared- 开头")
        runtime["env_name"] = requested_name or f"project-{project.public_id}-py{python_version.replace('.', '')}"

    @staticmethod
    async def _bind_worker_runtime(project, worker, runtime, *, conn) -> None:
        """把项目钉到运行时所在的 Worker 上。

        运行时是该 Worker 文件系统上的一个真实 venv，别的节点没有它，所以
        「环境在哪个节点」同时就是「任务必须派到哪个节点」——两者不是可以各自
        取值的独立配置。因此这里必须同时写运行时位置(worker_id, public_id)与
        调度绑定(bound_worker_id, 内部 id)；只写前者会让默认的 prefer 策略
        (execution_resolver._resolve_prefer_bound 只读 bound_worker_id)恒定
        落回按负载自动选节点，集群一有第二个 Worker 就 100% 派错。
        repository_import_service._create_project 一直是这么写的（两列同时写），
        它那条路因此从来没坏过；这里补齐同样的语义。
        """
        project.env_location = "worker"
        project.worker_id = runtime["worker_id"]
        project.worker_env_name = runtime["env_name"]
        project.python_version = runtime["python_version"]
        project.runtime_scope = runtime["scope"]
        project.runtime_kind = runtime["kind"]
        project.runtime_locator = None
        project.current_runtime_id = None
        project.bound_worker_id = worker.id
        await project.save(using_db=conn)
        logger.info(f"项目 {project.name} 绑定 Worker 运行时: {worker.name}/{runtime['env_name']}")


__all__ = ["ProjectRuntimeBindingMixin"]
