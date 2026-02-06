"""
项目服务层
处理项目相关的业务逻辑
"""

import os
import shutil
import tarfile
import zipfile

from fastapi import HTTPException, status
from loguru import logger
from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q

from antcode_core.common.hash_utils import calculate_content_hash
from antcode_core.common.utils.db_optimizer import DatabaseOptimizer
from antcode_core.common.utils.json_parser import parse_cookies, parse_headers
from antcode_core.domain.models import Project, ProjectCode, ProjectFile, ProjectRule, ProjectType
from antcode_core.domain.models.enums import CallbackType, RequestMethod, RuntimeScope
from antcode_core.domain.schemas.project import (
    ExtractionRule,
    PaginationConfig,
    TaskJsonRequest,
    TaskMeta,
)
from antcode_core.application.services.files.file_storage import file_storage_service
from antcode_core.application.services.workers import worker_service
from antcode_core.application.services.projects.relation_service import relation_service


class ProjectService:
    """项目服务类"""

    MAX_EXTRACT_SIZE = 500 * 1024 * 1024  # 500MB 展开体积上限
    MAX_EXTRACT_FILES = 2000  # 最大文件数限制

    @staticmethod
    def _is_safe_path(base_dir, target_path):
        """校验目标路径是否位于指定基准目录内"""
        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(target_path)
        return os.path.commonpath([abs_base, abs_target]) == abs_base

    @staticmethod
    def _resolve_full_path(path):
        """将存储路径解析为绝对路径"""
        if not path:
            return None
        if os.path.isabs(path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="禁止使用绝对路径，请迁移为存储相对路径",
            )
        return file_storage_service.get_file_path(path)

    @staticmethod
    def _is_compressed_filename(filename: str) -> bool:
        lower_name = (filename or "").lower()
        return lower_name.endswith((".zip", ".tar.gz", ".tar"))

    def _check_extract_limits_zip(self, zip_ref, base_dir):
        total_size = 0
        file_count = 0
        for info in zip_ref.infolist():
            member_path = os.path.abspath(os.path.join(base_dir, info.filename))
            if not self._is_safe_path(base_dir, member_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="压缩包包含非法路径，已拒绝",
                )
            if not info.is_dir():
                total_size += info.file_size
                file_count += 1
            if total_size > self.MAX_EXTRACT_SIZE or file_count > self.MAX_EXTRACT_FILES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="压缩包解压体积或文件数超出限制",
                )

    def _check_extract_limits_tar(self, tar_ref, base_dir):
        total_size = 0
        file_count = 0
        safe_members = []
        for member in tar_ref.getmembers():
            if member.issym() or member.islnk():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="压缩包包含符号链接，已拒绝",
                )
            member_path = os.path.abspath(os.path.join(base_dir, member.name))
            if not self._is_safe_path(base_dir, member_path):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="压缩包包含非法路径，已拒绝",
                )
            if member.isfile():
                total_size += member.size
                file_count += 1
            safe_members.append(member)
            if total_size > self.MAX_EXTRACT_SIZE or file_count > self.MAX_EXTRACT_FILES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="压缩包解压体积或文件数超出限制",
                )
        return safe_members

    async def create_project(self, request, user_id, file=None, files=None, code_file=None):
        """
        创建项目

        Args:
            request: 项目创建请求
            user_id: 用户ID
            file: 文件项目的文件（可选）
            code_file: 代码项目的代码文件（可选）

        Returns:
            Project: 创建的项目对象
        """
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

                # 配置项目环境（支持本地和 Worker 运行时）
                await self._setup_project_environment(project, request, user_id, conn)

                # 根据项目类型创建详情记录
                if request.type == ProjectType.FILE:
                    await self._create_file_project_detail(project, request, file, files, conn)
                elif request.type == ProjectType.RULE:
                    await self._create_rule_project_detail(project, request, conn)
                elif request.type == ProjectType.CODE:
                    await self._create_code_project_detail(project, request, code_file, conn)

                logger.info(f"项目创建成功: {project.name} (ID: {project.id})")
                return project

        except IntegrityError as e:
            logger.error(f"项目创建失败 - 完整性错误: {e}")
            if "name" in str(e):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="项目名称已存在")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="数据完整性错误")
        except Exception as e:
            logger.error(f"项目创建失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"项目创建失败: {str(e)}",
            )

    async def _create_file_project_detail(self, project, request, file, files=None, conn=None):
        """创建文件项目详情

        新架构要求：文件项目必须存储到 S3，禁止本地存储
        """
        from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

        # 新架构强制要求 S3 存储
        if not is_s3_storage_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="文件项目需要 S3 存储后端，请配置 FILE_STORAGE_BACKEND=s3",
            )

        if not file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="文件项目必须上传文件"
            )

        filename = file.filename or ""
        is_compressed = self._is_compressed_filename(filename)
        if is_compressed and not (request.entry_point and request.entry_point.strip()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="压缩包必须指定入口文件",
            )

        # 保存主文件到 S3
        (
            storage_path,
            file_hash,
            file_size,
            file_type,
        ) = await file_storage_service.save_file(file)

        # 检查是否为压缩包，如果是则立即解压到 S3
        extracted_path = None

        if is_compressed:
            # 解压到 S3 的 projects/{project_id}/ 前缀
            extracted_path = await self._extract_compressed_file(
                storage_path, filename, project.id
            )
            logger.info(f"压缩包已解压到 S3: {extracted_path}")

        # 处理附加文件
        additional_files_info = None
        total_file_count = 1

        if files and len(files) > 0:
            additional_files_info = []
            total_file_count += len(files)

            for additional_file in files:
                if additional_file.filename:
                    try:
                        (
                            add_storage_path,
                            add_file_hash,
                            add_file_size,
                            add_file_type,
                        ) = await file_storage_service.save_file(additional_file)

                        # 检查附加文件是否为压缩包
                        add_filename = additional_file.filename or ""
                        add_is_compressed = self._is_compressed_filename(add_filename)
                        add_extracted_path = None

                        if add_is_compressed:
                            add_extracted_path = await self._extract_compressed_file(
                                add_storage_path, add_filename, project.id
                            )
                            logger.info(f"附加压缩包已解压到 S3: {add_extracted_path}")

                        additional_files_info.append(
                            {
                                "original_name": additional_file.filename,
                                "file_path": add_extracted_path
                                if add_extracted_path
                                else add_storage_path,
                                "original_file_path": add_storage_path
                                if add_is_compressed
                                else None,
                                "file_size": add_file_size,
                                "file_type": add_file_type,
                                "file_hash": add_file_hash,
                                "is_compressed": add_is_compressed,
                            }
                        )

                    except Exception as e:
                        logger.error(f"保存附加文件失败: {additional_file.filename}, 错误: {e}")

        # 创建文件项目详情（强制 S3 存储类型）
        await ProjectFile.create(
            project_id=project.id,
            file_path=extracted_path if extracted_path else storage_path,
            original_file_path=storage_path if is_compressed else None,
            original_name=file.filename,
            file_size=file_size,
            file_type=file_type,
            file_hash=file_hash,
            entry_point=request.entry_point,
            runtime_config=request.runtime_config,
            environment_vars=request.environment_vars,
            storage_type="s3",  # 新架构强制 S3
            is_compressed=is_compressed,
            file_count=total_file_count,
            additional_files=additional_files_info,
            using_db=conn,
        )

    async def _create_rule_project_detail(self, project, request, conn=None):
        """创建规则项目详情"""

        # 验证提取规则必须存在
        if not request.extraction_rules:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供提取规则")

        # 解析headers和cookies数据
        headers_data = parse_headers(getattr(request, "headers", None))
        cookies_data = parse_cookies(getattr(request, "cookies", None))

        await ProjectRule.create(
            project_id=project.id,  # 使用应用层外键
            engine=request.engine,
            target_url=request.target_url,
            url_pattern=request.url_pattern,
            request_method=getattr(request, "request_method", RequestMethod.GET),
            callback_type=getattr(request, "callback_type", CallbackType.LIST),
            extraction_rules=[rule.dict() for rule in request.extraction_rules]
            if request.extraction_rules
            else None,
            pagination_config=request.pagination_config.dict()
            if request.pagination_config
            else None,
            max_pages=request.max_pages,
            start_page=getattr(request, "start_page", 1),
            request_delay=request.request_delay,
            priority=getattr(request, "priority", 0),
            headers=headers_data,
            cookies=cookies_data,
            using_db=conn,
        )

    async def _create_code_project_detail(self, project, request, code_file, conn=None):
        """创建代码项目详情"""
        code_content = None

        # 优先使用直接提交的代码内容
        if request.code_content:
            code_content = request.code_content
        elif code_file:
            # 如果没有直接提交代码内容，则从上传的文件中读取
            try:
                code_content = (await code_file.read()).decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="代码文件必须是UTF-8编码的文本文件",
                )
        else:
            # 既没有代码内容也没有上传文件
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="代码项目必须提供代码内容或上传代码文件",
            )

        # 验证代码内容不为空
        if not code_content or not code_content.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="代码内容不能为空")

        # 计算内容哈希
        content_hash = calculate_content_hash(code_content, "md5")

        # 创建代码项目详情
        await ProjectCode.create(
            project_id=project.id,  # 使用应用层外键
            content=code_content,
            language=request.language,
            version=request.version,
            content_hash=content_hash,
            entry_point=request.entry_point,
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
        """获取项目列表（优化版本）

        注意：不缓存此方法的结果，因为包含动态关联数据（用户信息）。
        缓存在 API 层通过 @fast_response 实现，TTL 较短（2分钟）。
        """
        from tortoise.functions import Count

        from antcode_core.domain.models.task import Task
        from antcode_core.application.services.base import QueryHelper

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
        user_projects = await Project.filter(id__in=project_ids, user_id=user_id).values_list(
            "id", flat=True
        )

        # 过滤出用户有权限的更新
        valid_updates = [
            {**update, "updated_by": user_id}
            for update in updates
            if update.get("id") in user_projects
        ]

        if not valid_updates:
            return 0

        optimizer = DatabaseOptimizer()
        return await optimizer.bulk_update(
            model_class=Project, updates=valid_updates, key_field="id"
        )

    async def batch_delete_projects(self, project_ids, user_id):
        """批量删除项目"""
        # 验证用户权限
        valid_ids = await Project.filter(id__in=project_ids, user_id=user_id).values_list(
            "id", flat=True
        )

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
            proxy=rule_detail.proxy_config.get("proxy") if rule_detail.proxy_config else None,
            task_id=task_id,
            worker_id=rule_detail.task_config.get("worker_id", "Scraper-Node-Default-01")
            if rule_detail.task_config
            else "Scraper-Node-Default-01",
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

        # 获取规则详情
        rule_detail = await relation_service.get_project_rule_detail(project_id)
        if not rule_detail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="规则项目配置不存在"
            )

        # 更新规则配置
        update_data = {}
        if request.target_url is not None:
            update_data["target_url"] = request.target_url
        if request.callback_type is not None:
            update_data["callback_type"] = request.callback_type
        if request.request_method is not None:
            update_data["request_method"] = request.request_method
        if request.extraction_rules is not None:
            update_data["extraction_rules"] = [rule.dict() for rule in request.extraction_rules]
        if request.pagination_config is not None:
            update_data["pagination_config"] = request.pagination_config.dict()
        if request.max_pages is not None:
            update_data["max_pages"] = request.max_pages
        if request.start_page is not None:
            update_data["start_page"] = request.start_page
        if request.request_delay is not None:
            update_data["request_delay"] = request.request_delay
        if request.priority is not None:
            update_data["priority"] = request.priority
        if request.dont_filter is not None:
            update_data["dont_filter"] = request.dont_filter
        if request.headers is not None:
            update_data["headers"] = request.headers
        if request.cookies is not None:
            update_data["cookies"] = request.cookies
        if request.proxy_config is not None:
            update_data["proxy_config"] = {"proxy": request.proxy_config}
        if request.task_config is not None:
            update_data["task_config"] = request.task_config

        # 应用更新
        if update_data:
            await rule_detail.update_from_dict(update_data)
            await rule_detail.save()

            # 更新项目的更新时间
            project.updated_by = user_id
            await project.save()

        return project

    async def update_code_config(self, project_id, request, user_id):
        """更新代码项目配置"""
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

        # 获取代码详情
        code_detail = await relation_service.get_project_code_detail(project_id)
        if not code_detail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="代码项目配置不存在"
            )

        # 构建更新数据
        update_data = {}

        if request.language is not None:
            update_data["language"] = request.language
        if request.version is not None:
            update_data["version"] = request.version
        if request.entry_point is not None:
            update_data["entry_point"] = request.entry_point
        if request.documentation is not None:
            update_data["documentation"] = request.documentation
        if request.code_content is not None:
            update_data["content"] = request.code_content
            # 重新计算内容哈希
            update_data["content_hash"] = calculate_content_hash(request.code_content, "md5")

        # 更新代码详情
        if update_data:
            await ProjectCode.filter(project_id=project_id).update(**update_data)

            # 更新项目的更新时间
            project.updated_by = user_id
            await project.save()

        return project

    async def update_file_config(self, project_id, request, user_id, file=None):
        """更新文件项目配置"""
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

        # 获取文件详情
        file_detail = await relation_service.get_project_file_detail(project_id)
        if not file_detail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="文件项目配置不存在"
            )

        # 构建更新数据
        update_data = {}

        if request.entry_point is not None:
            update_data["entry_point"] = request.entry_point
        if request.runtime_config is not None:
            update_data["runtime_config"] = request.runtime_config
        if request.environment_vars is not None:
            update_data["environment_vars"] = request.environment_vars

        # 处理文件替换
        if file:
            # 验证文件类型和大小
            if file.size > 100 * 1024 * 1024:  # 100MB限制
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="文件大小不能超过100MB",
                )

            # 检查文件类型
            allowed_extensions = [".py", ".zip", ".tar.gz", ".tar"]
            file_extension = None
            for ext in allowed_extensions:
                if file.filename.lower().endswith(ext):
                    file_extension = ext
                    break

            if not file_extension:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="不支持的文件类型，仅支持 .py, .zip, .tar.gz, .tar 格式",
                )

            # 删除旧文件或解压目录
            old_paths = {file_detail.file_path, file_detail.original_file_path}
            for storage_path in filter(None, old_paths):
                old_full_path = self._resolve_full_path(storage_path)
                if not old_full_path or not os.path.exists(old_full_path):
                    continue
                try:
                    if os.path.isdir(old_full_path):
                        shutil.rmtree(old_full_path, ignore_errors=True)
                    else:
                        await file_storage_service.delete_file(storage_path)
                    logger.info(f"删除旧文件: {old_full_path}")
                except Exception as e:
                    logger.warning(f"删除旧文件失败: {e}")

            is_compressed = self._is_compressed_filename(file.filename or "")
            if is_compressed:
                effective_entry_point = request.entry_point
                if effective_entry_point is None:
                    effective_entry_point = file_detail.entry_point
                if not effective_entry_point or not str(effective_entry_point).strip():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="压缩包必须指定入口文件",
                    )

            # 保存新文件
            storage_path, file_hash, file_size, file_type = await file_storage_service.save_file(file)
            extracted_path = None

            if is_compressed:
                extracted_path = await self._extract_compressed_file(
                    storage_path, file.filename or "", project.id
                )

            # 更新文件信息
            update_data.update(
                {
                    "file_path": extracted_path if extracted_path else storage_path,
                    "original_file_path": storage_path if is_compressed else None,
                    "original_name": file.filename,
                    "file_size": file_size,
                    "file_hash": file_hash,
                    "file_type": file_type,
                    "is_compressed": is_compressed,
                }
            )

        # 更新文件详情
        if update_data:
            await ProjectFile.filter(project_id=project_id).update(**update_data)

            # 更新项目的更新时间
            project.updated_by = user_id
            await project.save()

        return project

    async def _extract_compressed_file(self, storage_path, original_name, project_id):
        """解压压缩文件到 S3 项目目录

        新架构要求：所有文件项目必须存储到 S3
        解压后上传到 S3 的 projects/{project_id}/ 前缀
        """
        from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

        if not is_s3_storage_enabled():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="文件项目需要 S3 存储后端，请配置 FILE_STORAGE_BACKEND=s3",
            )

        try:
            return await self._extract_to_s3(storage_path, original_name, project_id)
        except Exception as e:
            logger.error(f"解压文件到 S3 失败: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"解压文件失败: {str(e)}",
            )

    async def _extract_to_local(self, storage_path, original_name, project_id):
        """解压到本地文件系统（已废弃，仅保留兼容性）

        新架构禁止使用本地存储，此方法仅供旧架构兼容
        """
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="新架构禁止本地存储文件项目，请配置 FILE_STORAGE_BACKEND=s3",
        )

    async def _extract_to_s3(self, storage_path, original_name, project_id):
        """解压到 S3

        1. 从 S3 下载压缩包到临时目录
        2. 解压到临时目录
        3. 上传解压后的文件到 S3 的 projects/{project_id}/ 前缀
        4. 清理临时文件
        """
        import tempfile

        from antcode_core.infrastructure.storage.base import get_file_storage_backend
        from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager

        backend = get_file_storage_backend()
        s3_manager = get_s3_client_manager()

        # S3 项目目录前缀
        s3_project_prefix = f"projects/{project_id}"

        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. 从 S3 下载压缩包
            temp_archive = os.path.join(temp_dir, original_name)
            try:
                file_bytes = await backend.get_file_bytes(storage_path)
                with open(temp_archive, "wb") as f:
                    f.write(file_bytes)
                logger.debug(f"从 S3 下载压缩包: {storage_path} -> {temp_archive}")
            except Exception as e:
                raise ValueError(f"从 S3 下载压缩包失败: {e}")

            # 2. 解压到临时目录
            extract_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            base_dir = os.path.abspath(extract_dir)

            lower_name = (original_name or "").lower()
            if lower_name.endswith(".zip"):
                with zipfile.ZipFile(temp_archive, "r") as zip_ref:
                    self._check_extract_limits_zip(zip_ref, base_dir)
                    zip_ref.extractall(extract_dir)
                    logger.info(f"ZIP文件解压完成: {temp_archive}")
            elif lower_name.endswith(".tar.gz"):
                with tarfile.open(temp_archive, "r:gz") as tar_ref:
                    safe_members = self._check_extract_limits_tar(tar_ref, base_dir)
                    tar_ref.extractall(extract_dir, members=safe_members)
                    logger.info(f"TAR.GZ文件解压完成: {temp_archive}")
            elif lower_name.endswith(".tar"):
                with tarfile.open(temp_archive, "r:") as tar_ref:
                    safe_members = self._check_extract_limits_tar(tar_ref, base_dir)
                    tar_ref.extractall(extract_dir, members=safe_members)
                    logger.info(f"TAR文件解压完成: {temp_archive}")
            else:
                raise ValueError(f"不支持的压缩格式: {original_name}")

            # 3. 上传解压后的文件到 S3
            try:
                uploaded = await s3_manager.upload_directory(
                    bucket=backend.bucket,
                    local_dir=extract_dir,
                    s3_prefix=s3_project_prefix,
                    max_files=self.MAX_EXTRACT_FILES,
                )
                logger.info(f"项目文件已上传到 S3: {s3_project_prefix}, 共 {len(uploaded)} 个文件")
            except Exception as e:
                raise ValueError(f"上传解压文件到 S3 失败: {e}")

        # 返回 S3 前缀路径
        return s3_project_prefix

    # ========== 环境配置方法 ==========

    async def _setup_project_environment(self, project, request, user_id, conn):
        """配置项目环境"""
        runtime_scope = getattr(request, "runtime_scope", None)

        if not runtime_scope:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="必须选择运行时作用域"
            )

        await self._setup_worker_environment(project, request, user_id, conn)

    async def _setup_worker_environment(self, project, request, user_id, conn):
        """配置 Worker 环境"""
        from antcode_core.domain.models.worker import Worker
        from antcode_core.application.services.runtime import runtime_control_service

        worker_id = getattr(request, "worker_id", None)
        if not worker_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="使用 Worker 运行时必须指定 worker_id",
            )

        # 验证 Worker 存在且在线
        worker = await Worker.get_or_none(public_id=worker_id)
        if not worker:
            raise HTTPException(status_code=404, detail="Worker 不存在")
        if worker.status != "online":
            raise HTTPException(status_code=400, detail=f"Worker {worker.name} 当前不在线")

        # 权限校验
        from antcode_core.application.services.users.user_service import user_service

        user_obj = await user_service.get_user_by_id(user_id)
        is_admin = bool(user_obj and user_obj.is_admin)
        created_by = user_obj.username if user_obj else str(user_id)
        if not is_admin:
            allowed = await worker_service.check_user_worker_permission(
                user_id=user_id,
                worker_id=worker.id,
                is_admin=False,
                required_permission="use",
            )
            if not allowed:
                raise HTTPException(status_code=403, detail="无 Worker 访问权限")

        use_existing_env = getattr(request, "use_existing_env", False)
        runtime_scope = request.runtime_scope
        python_version = getattr(request, "python_version", None)
        existing_env_name = getattr(request, "existing_env_name", None)
        shared_runtime_key = getattr(request, "shared_runtime_key", None)
        requested_env_name = getattr(request, "env_name", None)
        dependencies = getattr(request, "dependencies", None)
        if not isinstance(dependencies, list):
            dependencies = []

        env_name = None

        if use_existing_env:
            # 使用现有 Worker 运行时
            if not existing_env_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="使用现有环境时必须指定 existing_env_name",
                )
            env_name = existing_env_name

            env_result = await runtime_control_service.get_env(worker_id, env_name)
            if not env_result.get("success") or not env_result.get("data"):
                raise HTTPException(status_code=400, detail="指定环境不存在或不可用")

            env_info = env_result.get("data") or {}
            if runtime_scope == RuntimeScope.SHARED and not env_name.startswith("shared-"):
                raise HTTPException(status_code=400, detail="共享环境必须选择共享运行时")
            if runtime_scope == RuntimeScope.PRIVATE and env_name.startswith("shared-"):
                raise HTTPException(status_code=400, detail="私有环境不允许使用共享运行时")

            if not python_version and env_info.get("python_version"):
                python_version = env_info.get("python_version")
        else:
            if runtime_scope == RuntimeScope.SHARED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="共享环境必须从环境管理中选择现有环境",
                )
            # 创建新 Worker 运行时
            if not python_version:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="创建新环境时必须指定 python_version",
                )

            # 生成环境名称
            if runtime_scope == RuntimeScope.PRIVATE:
                if requested_env_name and requested_env_name.startswith("shared-"):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="私有环境名称不允许以 shared- 开头",
                    )
                env_name = requested_env_name or f"project-{project.public_id}-py{python_version.replace('.', '')}"
            else:  # 共享环境
                shared_key = shared_runtime_key or f"py{python_version.replace('.', '')}"
                env_name = f"shared-{shared_key}"

            create_result = await runtime_control_service.create_env(
                worker_id=worker_id,
                env_name=env_name,
                python_version=python_version,
                packages=dependencies,
                created_by=created_by,
            )
            if not create_result.get("success"):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"创建运行时失败: {create_result.get('error') or '未知错误'}",
                )

        # 更新项目的 Worker 运行时信息
        project.env_location = "worker"
        project.worker_id = worker_id
        project.worker_env_name = env_name
        project.python_version = python_version
        project.runtime_scope = runtime_scope
        # Worker 运行时不使用本地字段
        project.venv_path = None
        project.current_runtime_id = None
        await project.save(using_db=conn)

        logger.info(f"项目 {project.name} 绑定 Worker 运行时: {worker.name}/{env_name}")


# 创建项目服务实例
project_service = ProjectService()
