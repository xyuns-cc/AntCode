"""
项目服务层
处理项目相关的业务逻辑
"""

from fastapi import HTTPException, status
from loguru import logger
from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q

from antcode_core.application.services.projects.project_source_service import (
    project_source_service,
)
from antcode_core.application.services.projects.relation_service import relation_service
from antcode_core.application.services.workers import worker_service
from antcode_core.common.utils.db_optimizer import BulkUpdateOptions, DatabaseOptimizer
from antcode_core.common.utils.json_parser import parse_cookies, parse_headers
from antcode_core.domain.models import Project, ProjectCode, ProjectFile, ProjectRule, ProjectType
from antcode_core.domain.models.enums import CallbackType, RequestMethod, RuntimeKind, RuntimeScope
from antcode_core.domain.schemas.project import (
    ExtractionRule,
    PaginationConfig,
    TaskJsonRequest,
    TaskMeta,
)


class ProjectService:
    """项目服务类"""

    @staticmethod
    def _clean_runtime_config(runtime_config):
        if not runtime_config:
            return {}
        cleaned = dict(runtime_config)
        return cleaned

    @staticmethod
    def _build_rule_detail_payload(request):
        headers_data = parse_headers(getattr(request, "headers", None))
        cookies_data = parse_cookies(getattr(request, "cookies", None))
        return {
            "engine": request.engine,
            "target_url": request.target_url,
            "url_pattern": request.url_pattern,
            "request_method": getattr(request, "request_method", RequestMethod.GET),
            "callback_type": getattr(request, "callback_type", CallbackType.LIST),
            "extraction_rules": [rule.dict() for rule in request.extraction_rules]
            if request.extraction_rules
            else None,
            "data_schema": getattr(request, "data_schema", None),
            "pagination_config": request.pagination_config.dict() if request.pagination_config else None,
            "max_pages": request.max_pages,
            "start_page": getattr(request, "start_page", 1),
            "request_delay": request.request_delay,
            "retry_count": getattr(request, "retry_count", 3),
            "timeout": getattr(request, "timeout", 30),
            "priority": getattr(request, "priority", 0),
            "dont_filter": getattr(request, "dont_filter", False),
            "headers": headers_data,
            "cookies": cookies_data,
            "proxy_config": getattr(request, "proxy_config", None),
            "anti_spider": getattr(request, "anti_spider", None),
            "task_config": getattr(request, "task_config", None),
            # S10: scrapy-redis 断点续爬 + 内容级去重
            "resume_enabled": bool(getattr(request, "resume_enabled", None) or False),
            "dedup_config": getattr(request, "dedup_config", None),
        }

    @staticmethod
    def _build_rule_update_payload(request):
        update_data = {}
        scalar_fields = (
            "engine",
            "target_url",
            "url_pattern",
            "callback_type",
            "request_method",
            "max_pages",
            "start_page",
            "request_delay",
            "retry_count",
            "timeout",
            "priority",
            "dont_filter",
            # S10
            "resume_enabled",
        )
        for field in scalar_fields:
            value = getattr(request, field, None)
            if value is not None:
                update_data[field] = value

        if request.extraction_rules is not None:
            update_data["extraction_rules"] = [rule.dict() for rule in request.extraction_rules]
        if request.pagination_config is not None:
            update_data["pagination_config"] = request.pagination_config.dict()

        clearable_fields = (
            "data_schema",
            "headers",
            "cookies",
            "proxy_config",
            "anti_spider",
            "task_config",
            # S10
            "dedup_config",
        )
        for field in clearable_fields:
            if field in request.model_fields_set:
                update_data[field] = getattr(request, field)

        return update_data

    @staticmethod
    def _resolve_rule_proxy(rule_detail):
        proxy_config = getattr(rule_detail, "proxy_config", None)
        if not isinstance(proxy_config, dict):
            return None
        if proxy_config.get("enabled") is False:
            return None
        return proxy_config.get("proxy_url") or proxy_config.get("proxy")

    def _build_runtime_config(self, current_runtime_config, request_runtime_config):
        base_runtime_config = request_runtime_config
        if base_runtime_config is None:
            base_runtime_config = current_runtime_config
        return self._clean_runtime_config(base_runtime_config)

    def _build_file_detail_payload(
        self,
        request,
        *,
        current_detail=None,
    ):
        entry_point = getattr(request, "entry_point", None)
        if entry_point is None and current_detail is not None:
            entry_point = current_detail.entry_point
        if not entry_point or not str(entry_point).strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git 文件项目必须指定入口文件",
            )
        runtime_config = self._build_runtime_config(
            getattr(current_detail, "runtime_config", None),
            getattr(request, "runtime_config", None),
        )
        environment_vars = getattr(request, "environment_vars", None)
        if environment_vars is None and current_detail is not None:
            environment_vars = current_detail.environment_vars
        return {
            "entry_point": entry_point,
            "runtime_config": runtime_config,
            "environment_vars": environment_vars,
        }

    async def _bind_project_source(self, project_id, owner_user_id, request):
        repository = await project_source_service._get_enabled_repository(
            request.repository_id,
            owner_user_id,
        )
        source = await project_source_service.upsert_source(
            project_id=project_id,
            repository_id=repository.id,
            ref=getattr(request, "ref", None) or repository.default_ref,
            subdir=request.subdir,
            include_paths=getattr(request, "include_paths", None) or [],
        )
        return repository, source

    async def create_project(self, request, user_id):
        """在单个事务中创建项目、运行时与类型详情。"""
        from tortoise.transactions import in_transaction

        try:
            # 使用数据库事务确保原子性
            async with in_transaction() as conn:
                # 创建项目主记录（包含环境占位字段）
                project = await Project.create(
                    name=request.name,
                    description=request.description,
                    type=request.type,
                    tags=request.tags or [],
                    dependencies=request.dependencies,
                    user_id=user_id,
                    updated_by=user_id,
                    using_db=conn,
                )

                # 配置项目 Worker 运行时
                await self._setup_project_environment(project, request, user_id, conn)

                # 根据项目类型创建详情记录
                if request.type == ProjectType.FILE:
                    await self._create_file_project_detail(project, request, conn)
                elif request.type == ProjectType.RULE:
                    await self._create_rule_project_detail(project, request, conn)
                elif request.type == ProjectType.CODE:
                    await self._create_code_project_detail(project, request, conn)

                logger.info(f"项目创建成功: {project.name} (ID: {project.id})")

                await self._attach_project_creator(project)
                return project

        except IntegrityError as e:
            logger.error(f"项目创建失败 - 完整性错误: {e}")
            if "name" in str(e):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="项目名称已存在")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="数据完整性错误")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"项目创建失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"项目创建失败: {str(e)}",
            )

    @staticmethod
    async def _attach_project_creator(project):
        from antcode_core.application.services.users.user_service import user_service

        creator = await user_service.get_user_by_id(project.user_id)
        project.created_by_public_id = creator.public_id if creator else None
        project.created_by_username = creator.username if creator else None

    async def _create_file_project_detail(self, project, request, conn=None):
        """创建文件项目详情。"""
        await self._bind_project_source(project.id, project.user_id, request)
        detail_payload = self._build_file_detail_payload(request)
        await ProjectFile.create(
            project_id=project.id,
            using_db=conn,
            **detail_payload,
        )

    async def _create_rule_project_detail(self, project, request, conn=None):
        """创建规则项目详情"""
        if not request.extraction_rules:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供提取规则")
        await ProjectRule.create(
            project_id=project.id,  # 使用应用层外键
            using_db=conn,
            **self._build_rule_detail_payload(request),
        )

    async def _create_code_project_detail(self, project, request, conn=None):
        """创建代码项目详情。"""
        entry_point = request.entry_point
        if not entry_point or not str(entry_point).strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git 代码项目必须提供 entry_point",
            )
        await self._bind_project_source(project.id, project.user_id, request)
        runtime_config = self._build_runtime_config(
            None,
            getattr(request, "runtime_config", None),
        )

        await ProjectCode.create(
            project_id=project.id,
            language=request.language,
            entry_point=entry_point,
            runtime_config=runtime_config,
            environment_vars=getattr(request, "environment_vars", None),
            documentation=request.documentation,
            using_db=conn,
        )

    async def get_project_by_id(self, project_id, user_id=None):
        """根据ID获取项目（支持 public_id 和内部 id）"""
        from antcode_core.application.services.users.user_service import user_service

        # 尝试解析 project_id
        try:
            internal_id = int(project_id)
            query = Project.filter(id=internal_id)
        except (ValueError, TypeError):
            query = Project.filter(public_id=str(project_id))

        # 如果指定了用户ID，检查是否为管理员
        if user_id is not None:
            user = await user_service.get_user_by_id(user_id)

            # 如果不是管理员，则只能查看自己的项目
            if not (user and user.is_admin):
                query = query.filter(user_id=user_id)

        project = await query.first()
        if not project:
            return None

        # 获取创建者信息
        creator = await user_service.get_user_by_id(project.user_id)
        project.created_by_username = creator.username if creator else None
        project.created_by_public_id = creator.public_id if creator else None

        # 获取绑定 Worker 名称（执行策略相关）
        if project.bound_worker_id:
            from antcode_core.domain.models import Worker

            bound_worker = await Worker.get_or_none(id=project.bound_worker_id)
            project.bound_worker_name = bound_worker.name if bound_worker else None
        else:
            project.bound_worker_name = None

        return project

    async def get_projects_list(
        self,
        page=1,
        size=20,
        project_type=None,
        status=None,
        tag=None,
        user_id=None,
        search=None,
        worker_id=None,
    ):
        """分页查询项目，并批量附加创建者与任务数量。"""
        from tortoise.functions import Count

        from antcode_core.application.services.base import QueryHelper
        from antcode_core.domain.models.task import Task

        query = Project.all()

        # 构建过滤条件
        if project_type:
            query = query.filter(type=project_type)
        if status:
            query = query.filter(status=status)
        if user_id:
            query = query.filter(user_id=user_id)
        if tag:
            query = query.filter(tags__contains=tag)
        if worker_id:
            query = query.filter(worker_id=worker_id)

        # 关键字搜索
        if search:
            keyword = search.strip()
            if keyword:
                query = query.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword))

        # 使用 QueryHelper.paginate 进行统一分页查询
        projects, total = await QueryHelper.paginate(query, page, size, order_by="-created_at")

        # 使用 QueryHelper.batch_get_user_info 批量获取创建者信息
        user_ids = list({p.user_id for p in projects if p.user_id})
        users_map = await QueryHelper.batch_get_user_info(user_ids)

        # 批量查询每个项目的任务数
        project_ids = [p.id for p in projects]
        task_counts = {}
        if project_ids:
            task_count_results = (
                await Task.filter(project_id__in=project_ids)
                .group_by("project_id")
                .annotate(count=Count("id"))
                .values("project_id", "count")
            )
            task_counts = {r["project_id"]: r["count"] for r in task_count_results}

        # 为项目添加创建者信息和任务数
        for project in projects:
            user_info = users_map.get(project.user_id, {})
            project.created_by_username = user_info.get("username")
            project.created_by_public_id = user_info.get("public_id")
            project.task_count = task_counts.get(project.id, 0)

        return projects, total

    async def update_project(self, project_id, request, user_id):
        """更新项目"""
        from antcode_core.application.services.users.user_service import user_service

        # 检查用户是否为管理员
        user = await user_service.get_user_by_id(user_id)

        if user and user.is_admin:
            # 管理员可以更新所有项目
            project = await Project.filter(id=project_id).first()
        else:
            # 普通用户只能更新自己的项目
            project = await Project.filter(id=project_id, user_id=user_id).first()

        if not project:
            return None

        # 更新字段
        update_data = request.dict(exclude_unset=True)
        if update_data:
            update_data["updated_by"] = user_id

            # 处理执行策略相关字段
            if "bound_worker_id" in update_data:
                bound_worker_id = update_data["bound_worker_id"]
                if bound_worker_id:
                    # 将 public_id 转换为内部 id
                    from antcode_core.domain.models import Worker

                    worker = await Worker.get_or_none(public_id=str(bound_worker_id))
                    if worker:
                        update_data["bound_worker_id"] = worker.id
                    else:
                        # 尝试直接作为内部 id 使用
                        try:
                            update_data["bound_worker_id"] = int(bound_worker_id)
                        except (ValueError, TypeError):
                            update_data["bound_worker_id"] = None
                else:
                    update_data["bound_worker_id"] = None

            await project.update_from_dict(update_data)
            await project.save()

        return project

    async def delete_project(self, project_id, user_id):
        """删除项目"""
        from antcode_core.application.services.users.user_service import user_service

        # 检查用户是否为管理员
        user = await user_service.get_user_by_id(user_id)

        if user and user.is_admin:
            # 管理员可以删除所有项目
            project = await Project.filter(public_id=project_id).first()
        else:
            # 普通用户只能删除自己的项目
            project = await Project.filter(public_id=project_id, user_id=user_id).first()

        if not project:
            return False

        # 使用应用层级联删除
        deleted_counts = await relation_service.delete_project_cascade(project.id)
        logger.info(f"项目 {project_id} 级联删除完成: {deleted_counts}")
        return True

    async def batch_update_projects(self, updates, user_id):
        """批量更新项目"""
        # 验证用户权限
        project_ids = [update.get("id") for update in updates if "id" in update]
        user_projects = await Project.filter(id__in=project_ids, user_id=user_id).values_list("id", flat=True)

        # 过滤出用户有权限的更新
        valid_updates = [{**update, "updated_by": user_id} for update in updates if update.get("id") in user_projects]

        if not valid_updates:
            return 0

        optimizer = DatabaseOptimizer()
        return await optimizer.bulk_update(
            model_class=Project,
            updates=valid_updates,
            options=BulkUpdateOptions(key_field="id"),
        )

    async def batch_delete_projects(self, project_ids, user_id):
        """批量删除项目"""
        # 验证用户权限
        valid_ids = await Project.filter(id__in=project_ids, user_id=user_id).values_list("id", flat=True)

        if not valid_ids:
            return 0

        # 使用关系服务进行级联删除
        deleted_count = 0
        for project_id in valid_ids:
            try:
                deleted_counts = await relation_service.delete_project_cascade(project_id)
                deleted_count += 1
                logger.debug(f"删除项目 {project_id}: {deleted_counts}")
            except Exception as e:
                logger.error(f"删除项目 {project_id} 失败: {e}")

        return deleted_count

    async def generate_task_json(self, rule_detail):
        """根据规则项目配置生成任务JSON"""
        import random
        import string
        from datetime import datetime

        # 构建提取规则
        rules = []
        if rule_detail.extraction_rules:
            # 使用新的extraction_rules格式
            for rule_data in rule_detail.extraction_rules:
                rules.append(ExtractionRule(**rule_data))

        # 构建分页配置
        pagination_config = None
        if rule_detail.pagination_config:
            pagination_config = PaginationConfig(**rule_detail.pagination_config)

        # 生成任务ID
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]  # 精确到毫秒
        random_hex = "".join(random.choices(string.hexdigits.lower(), k=7))
        task_id = f"crawler-{timestamp}-{random_hex}"

        # 构建任务元数据
        task_meta = TaskMeta(
            fetch_type=rule_detail.engine,
            pagination=pagination_config,
            rules=rules,
            page_number=1 if pagination_config else None,
            proxy=self._resolve_rule_proxy(rule_detail),
            task_id=task_id,
            worker_id=rule_detail.task_config.get("worker_id", "Scraper-Worker-Default-01")
            if rule_detail.task_config
            else "Scraper-Worker-Default-01",
        )

        # 构建任务JSON
        task_json = TaskJsonRequest(
            url=rule_detail.target_url,
            callback=rule_detail.callback_type,
            method=rule_detail.request_method,
            meta=task_meta,
            headers=rule_detail.headers,
            cookies=rule_detail.cookies,
            priority=rule_detail.priority,
            dont_filter=rule_detail.dont_filter,
        )

        return task_json

    async def update_rule_config(self, project_id, request, user_id):
        """更新规则项目配置"""
        project = await self.get_project_by_id(project_id, user_id)
        if not project:
            return None

        rule_detail = await relation_service.get_project_rule_detail(project.id)
        if not rule_detail:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="规则项目配置不存在")

        update_data = self._build_rule_update_payload(request)
        if update_data:
            await rule_detail.update_from_dict(update_data)
            await rule_detail.save()
            project.updated_by = user_id
            await project.save()

        return project

    async def update_code_config(self, project_id, request, user_id):
        """更新代码项目配置。"""
        project = await self.get_project_by_id(project_id, user_id)
        if not project:
            return None

        code_detail = await relation_service.get_project_code_detail(project.id)
        if not code_detail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="代码项目配置不存在",
            )

        update_data = {
            "language": request.language if request.language is not None else code_detail.language,
            "documentation": (
                request.documentation if request.documentation is not None else code_detail.documentation
            ),
            "environment_vars": (
                request.environment_vars
                if getattr(request, "environment_vars", None) is not None
                else code_detail.environment_vars
            ),
        }

        entry_point = request.entry_point or code_detail.entry_point
        if not entry_point or not str(entry_point).strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git 代码项目必须提供 entry_point",
            )
        source = await project_source_service.get_source(project.id)
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="项目缺少 project_sources 配置",
            )
        update_data.update(
            {
                "entry_point": entry_point,
                "runtime_config": self._build_runtime_config(
                    code_detail.runtime_config,
                    getattr(request, "runtime_config", None),
                ),
            }
        )

        await code_detail.update_from_dict(update_data)
        await code_detail.save()
        project.updated_by = user_id
        await project.save()
        return project

    async def update_file_config(self, project_id, request, user_id):
        """更新文件项目配置。"""
        project = await self.get_project_by_id(project_id, user_id)
        if not project:
            return None

        file_detail = await relation_service.get_project_file_detail(project.id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件项目配置不存在",
            )

        if await project_source_service.get_source(project.id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="项目缺少 project_sources 配置",
            )
        update_data = self._build_file_detail_payload(
            request,
            current_detail=file_detail,
        )

        await file_detail.update_from_dict(update_data)
        await file_detail.save()
        project.updated_by = user_id
        await project.save()
        return project

    # ========== 环境配置方法 ==========

    async def _setup_project_environment(self, project, request, user_id, conn):
        """配置项目环境"""
        runtime_scope = getattr(request, "runtime_scope", None)

        if not runtime_scope:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须选择运行时作用域")

        await self._setup_worker_environment(project, request, user_id, conn)

    async def _setup_worker_environment(self, project, request, user_id, conn):
        """配置 Worker 环境"""
        from antcode_core.application.services.runtime import runtime_control_service

        worker, created_by, is_admin = await self._authorize_worker(request, user_id)
        runtime = self._worker_runtime_request(request)
        self._require_dependency_install_permission(runtime, is_admin)
        runtime["created_by"] = created_by
        runtime["is_admin"] = is_admin
        await self._resolve_worker_environment(project, runtime, runtime_control_service)
        await self._bind_worker_runtime(project, worker, runtime, conn)

    async def _authorize_worker(self, request, user_id):
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
    def _require_dependency_install_permission(runtime, is_admin):
        if runtime["use_existing"] or not runtime["dependencies"] or is_admin:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可在项目创建时安装 Worker 依赖；普通用户必须使用无依赖的新环境或现有环境",
        )

    @staticmethod
    def _worker_runtime_request(request):
        runtime_kind = getattr(request, "runtime_kind", RuntimeKind.PYTHON)
        if runtime_kind != RuntimeKind.PYTHON:
            raise HTTPException(status_code=400, detail="当前仅支持 python 运行时")
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

    async def _resolve_worker_environment(self, project, runtime, runtime_service):
        if runtime["use_existing"]:
            await self._select_existing_environment(runtime, runtime_service)
            return
        await self._create_worker_environment(project, runtime, runtime_service)

    @staticmethod
    async def _select_existing_environment(runtime, runtime_service):
        env_name = runtime["existing_name"]
        if not env_name:
            raise HTTPException(status_code=400, detail="使用现有环境时必须指定 existing_env_name")
        result = await runtime_service.get_env(runtime["worker_id"], env_name)
        if not result.get("success") or not result.get("data"):
            raise HTTPException(status_code=400, detail="指定环境不存在或不可用")
        env = result["data"]
        env_scope = env.get("scope")
        if env_scope not in {RuntimeScope.SHARED.value, RuntimeScope.PRIVATE.value}:
            raise HTTPException(status_code=500, detail="Worker 返回的运行时作用域无效")
        if env_scope == RuntimeScope.PRIVATE.value and not runtime["is_admin"]:
            if env.get("created_by") != runtime["created_by"]:
                raise HTTPException(status_code=403, detail="无权绑定其他用户的私有运行时环境")
        if runtime["scope"] == RuntimeScope.SHARED and env_scope != RuntimeScope.SHARED.value:
            raise HTTPException(status_code=400, detail="共享环境必须选择共享运行时")
        if runtime["scope"] == RuntimeScope.PRIVATE and env_scope != RuntimeScope.PRIVATE.value:
            raise HTTPException(status_code=400, detail="私有环境不允许使用共享运行时")
        runtime["env_name"] = env_name
        runtime["python_version"] = runtime["python_version"] or env.get("python_version")

    @staticmethod
    async def _create_worker_environment(project, runtime, runtime_service):
        if runtime["scope"] == RuntimeScope.SHARED:
            raise HTTPException(status_code=400, detail="共享环境必须从环境管理中选择现有环境")
        python_version = runtime["python_version"]
        if not python_version:
            raise HTTPException(status_code=400, detail="创建新环境时必须指定 python_version")
        requested_name = runtime["requested_name"]
        if requested_name and requested_name.startswith("shared-"):
            raise HTTPException(status_code=400, detail="私有环境名称不允许以 shared- 开头")
        runtime["env_name"] = requested_name or f"project-{project.public_id}-py{python_version.replace('.', '')}"
        result = await runtime_service.create_env(
            worker_id=runtime["worker_id"],
            env_name=runtime["env_name"],
            python_version=python_version,
            packages=runtime["dependencies"],
            created_by=runtime["created_by"],
        )
        if not result.get("success"):
            detail = result.get("error") or "未知错误"
            raise HTTPException(status_code=500, detail=f"创建运行时失败: {detail}")

    @staticmethod
    async def _bind_worker_runtime(project, worker, runtime, conn):
        project.env_location = "worker"
        project.worker_id = runtime["worker_id"]
        project.worker_env_name = runtime["env_name"]
        project.python_version = runtime["python_version"]
        project.runtime_scope = runtime["scope"]
        project.runtime_kind = runtime["kind"]
        project.runtime_locator = None
        project.current_runtime_id = None
        await project.save(using_db=conn)
        logger.info(f"项目 {project.name} 绑定 Worker 运行时: {worker.name}/{runtime['env_name']}")


# 创建项目服务实例
project_service = ProjectService()
