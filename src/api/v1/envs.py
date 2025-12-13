from fastapi import APIRouter, HTTPException, status, Depends, Query, Body
from pydantic import BaseModel
from loguru import logger

from src.schemas.envs import (
    InterpreterInfo,
    PythonVersionListResponse,
    VenvStatusResponse,
    VenvListItem,
)
from src.services.envs.python_env_service import python_env_service
from src.services.envs.venv_service import project_venv_service
from src.models import Project
from src.models.enums import VenvScope
from src.core.security.auth import get_current_user_id, get_current_user
from src.core.response import page as page_response
from src.schemas.common import PaginationResponse
from src.models import Venv, Interpreter, ProjectVenvBinding, User
from tortoise.expressions import Q
from src.core.config import settings
from src.utils.api_optimizer import fast_response


router = APIRouter(tags=["环境管理"])


class EnvConfigResponse(BaseModel):
    venv_root: str
    mise_root: str


@router.get("/python/versions", response_model=PythonVersionListResponse)
@fast_response(cache_ttl=300, namespace="envs:versions")
async def list_python_versions():
    """列出 mise 支持的 Python 版本。"""
    try:
        versions = await python_env_service.list_remote_versions()
        return PythonVersionListResponse(versions=versions)
    except Exception as e:
        # 如果缺少 mise 或调用失败，返回空列表而非报错
        msg = str(e)
        is_mise_missing = (
            isinstance(e, FileNotFoundError)
            or ("No such file or directory" in msg and "mise" in msg)
        )
        if is_mise_missing:
            logger.warning("未检测到 mise，返回空版本列表")
            return PythonVersionListResponse(versions=[])
        logger.error(f"获取版本失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="获取版本失败"
        )


@router.get("/python/interpreters", response_model=list[InterpreterInfo])
@fast_response(cache_ttl=120, namespace="envs:interpreters")
async def list_python_interpreters(source=Query(None)):
    """列出已注册的 Python 解释器（来自数据库，可按来源过滤）。"""
    try:
        items = await python_env_service.list_db(source)
        return [InterpreterInfo(**x) for x in items]
    except Exception as e:
        logger.error(f"获取已安装解释器失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        )


@router.post(
    "/python/interpreters",
    response_model=InterpreterInfo,
    status_code=status.HTTP_201_CREATED
)
async def install_python_interpreter(req, current_user_id=Depends(get_current_user_id)):
    """安装指定 Python 版本（幂等）。"""
    try:
        info = await python_env_service.ensure_installed(
            req.version, created_by=current_user_id
        )
        return InterpreterInfo(**info)
    except Exception as e:
        logger.error(f"安装解释器失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )


@router.post(
    "/python/interpreters/local",
    response_model=InterpreterInfo,
    status_code=status.HTTP_201_CREATED
)
async def register_local_interpreter(
    python_bin=Body(..., embed=True),
    current_user_id=Depends(get_current_user_id)
):
    """注册本地 Python 解释器。"""
    try:
        info = await python_env_service.register_local(python_bin, created_by=current_user_id)
        # 返回包含source信息
        return InterpreterInfo(**{**info, "source": "local"})
    except Exception as e:
        logger.error(f"注册本地解释器失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/python/interpreters/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_python_interpreter(version, source=Query("mise")):
    """卸载或删除解释器：source=mise 执行卸载；source=local/system 删除登记记录。"""
    try:
        if source in ("local", "system"):
            from src.models import Interpreter
            deleted = await Interpreter.filter(tool="python", version=version, source=source).delete()
            if deleted == 0:
                raise HTTPException(status_code=404, detail=f"未找到 {source} 解释器记录")
            return
        await python_env_service.uninstall(version)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"卸载解释器失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/projects/{project_id}/venv", response_model=VenvStatusResponse)
async def get_project_venv(project_id):
    """获取项目虚拟环境状态（以数据库绑定为准）。"""
    try:
        # 支持 public_id 和内部 id
        try:
            internal_id = int(project_id)
            project = await Project.get(id=internal_id)
        except (ValueError, TypeError):
            project = await Project.get(public_id=str(project_id))

        return VenvStatusResponse(
            project_id=project.public_id,
            scope=project.venv_scope,
            version=project.python_version,
            venv_path=project.venv_path,
        )
    except Exception as e:
        logger.error(f"获取 venv 状态失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/projects/{project_id}/venv", response_model=VenvStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_or_bind_project_venv(project_id, req, current_user_id=Depends(get_current_user_id)):
    """统一接口：根据 venv_scope 创建/复用并绑定项目的私有或共享虚拟环境。"""
    try:
        if req.venv_scope == VenvScope.PRIVATE:
            info = await project_venv_service.create_or_use(
                str(project_id),
                req.version,
                req.create_if_missing,
                created_by=current_user_id,
                interpreter_source=req.interpreter_source,
                python_bin=req.python_bin,
            )
            venv_path = info["venv_path"]
        elif req.venv_scope == VenvScope.SHARED:
            shared = await project_venv_service.create_or_use_shared(
                req.version,
                req.shared_venv_key,
                created_by=current_user_id,
                interpreter_source=req.interpreter_source,
                python_bin=req.python_bin,
            )
            venv_path = shared["venv_path"]
        else:
            raise HTTPException(status_code=400, detail="不支持的虚拟环境作用域")

        # 写入项目绑定 - 支持 public_id 和内部 id
        try:
            internal_id = int(project_id)
            project = await Project.get(id=internal_id)
        except (ValueError, TypeError):
            project = await Project.get(public_id=str(project_id))

        project.python_version = req.version
        project.venv_scope = req.venv_scope
        project.venv_path = venv_path
        # 设置规范绑定
        try:
            venv_obj = await Venv.get_or_none(venv_path=venv_path)
            if venv_obj:
                project.current_venv_id = venv_obj.id
        except Exception:
            pass
        await project.save()

        return VenvStatusResponse(project_id=project.public_id, scope=req.venv_scope, version=req.version, venv_path=venv_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建/绑定 venv 失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/projects/{project_id}/venv", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_venv(project_id):
    """删除项目 venv（不影响解释器）。"""
    try:
        await project_venv_service.delete(project_id)
    except Exception as e:
        logger.error(f"删除 venv 失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/projects/{project_id}/venv/packages")
async def list_project_venv_packages(project_id):
    """列出项目虚拟环境中的依赖包。"""
    try:
        # 支持 public_id 和内部 id
        try:
            internal_id = int(project_id)
            project = await Project.get(id=internal_id)
        except (ValueError, TypeError):
            project = await Project.get(public_id=str(project_id))

        if not project.venv_path:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目未绑定虚拟环境")
        items = await project_venv_service.list_packages(project.venv_path)
        return {"project_id": project.public_id, "packages": items}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取依赖列表失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/venvs", response_model=PaginationResponse[VenvListItem])
@fast_response(cache_ttl=120, namespace="envs:list")
async def list_venvs(
    scope: str = None,
    project_id: int = None,
    q: str = None,
    page: int = 1,
    size: int = 20,
    include_packages: bool = False,
    limit_packages: int = 50,
    interpreter_source: str = None,
    node_id: str = Query(None, description="节点ID筛选"),
    current_user=Depends(get_current_user),
):
    """分页列出虚拟环境，支持按作用域、项目和关键字过滤；可选附带依赖列表。"""
    from src.models import Node

    try:
        query = Venv.all().prefetch_related("interpreter")
        if scope is not None:
            query = query.filter(scope=scope)
        if project_id is not None:
            # 仅取当前绑定的环境
            venv_ids = await ProjectVenvBinding.filter(project_id=project_id, is_current=True).values_list("venv_id", flat=True)
            query = query.filter(id__in=list(venv_ids))
        if q:
            query = query.filter(
                Q(venv_path__icontains=q) | Q(key__icontains=q) | Q(version__icontains=q)
            )
        if interpreter_source:
            query = query.filter(interpreter__source=interpreter_source)

        # 节点筛选
        if node_id:
            node = await Node.filter(public_id=node_id).first()
            if node:
                query = query.filter(node_id=node.id)
            else:
                try:
                    query = query.filter(node_id=int(node_id))
                except ValueError:
                    pass

        # 权限控制：非管理员仅可见自己创建的共享环境 + 自己项目绑定的私有环境
        user = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(user and getattr(user, 'is_admin', False))
        if not is_admin:
            # 自己创建的共享/私有
            created_filter = Q(created_by=current_user.user_id)
            # 自己的项目绑定的私有环境（Project 已在文件顶部导入）
            my_project_ids = await Project.filter(user_id=current_user.user_id).values_list('id', flat=True)
            my_venv_ids = []
            if my_project_ids:
                my_venv_ids = await ProjectVenvBinding.filter(project_id__in=list(my_project_ids), is_current=True).values_list('venv_id', flat=True)
            if my_venv_ids:
                query = query.filter(created_filter | Q(id__in=list(my_venv_ids)))
            else:
                query = query.filter(created_filter)

        total = await query.count()
        items = await query.offset((page - 1) * size).limit(size)

        # 预取创建者用户名，避免在循环中逐个查询
        creator_ids = {v.created_by for v in items if getattr(v, 'created_by', None)}
        user_map = {}
        if creator_ids:
            users = await User.filter(id__in=list(creator_ids)).values("id", "username")
            user_map = {u["id"]: u["username"] for u in users}

        data: list[VenvListItem] = []
        for v in items:
            interpreter = await Interpreter.get(id=v.interpreter_id)
            # 取当前绑定的项目（如存在）- 使用 first() 避免多记录异常
            current_binding = await ProjectVenvBinding.filter(venv_id=v.id, is_current=True).first()
            # 获取当前绑定项目的 public_id
            current_project_public_id = None
            if current_binding:
                bound_project = await Project.get_or_none(id=current_binding.project_id)
                current_project_public_id = bound_project.public_id if bound_project else None

            # 获取创建者的 public_id
            created_by_public_id = None
            if getattr(v, 'created_by', None):
                creator = await User.get_or_none(id=v.created_by)
                created_by_public_id = creator.public_id if creator else None

            item = VenvListItem(
                id=v.public_id,
                scope=v.scope,
                key=v.key,
                version=v.version,
                venv_path=v.venv_path,
                interpreter_version=interpreter.version,
                interpreter_source=interpreter.source,
                python_bin=interpreter.python_bin,
                install_dir=interpreter.install_dir,
                created_by=created_by_public_id,
                created_by_username=user_map.get(v.created_by) if getattr(v, 'created_by', None) else None,
                created_at=v.created_at.isoformat() if v.created_at else None,
                updated_at=v.updated_at.isoformat() if v.updated_at else None,
                current_project_id=current_project_public_id,
            )
            if include_packages:
                try:
                    pkgs = await project_venv_service.list_packages(v.venv_path)
                    if limit_packages and isinstance(pkgs, list):
                        pkgs = pkgs[:limit_packages]
                    item.packages = pkgs
                except Exception as e:
                    logger.warning(f"获取依赖失败 venv={v.id}: {e}")
            data.append(item)

        return page_response(items=data, total=total, page=page, size=size)
    except Exception as e:
        logger.error(f"列出虚拟环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/venvs/{venv_id}/packages")
@fast_response(cache_ttl=60, namespace="envs:packages", key_prefix_fn=lambda args, kwargs: str(kwargs.get('venv_id') if 'venv_id' in kwargs else (args[0] if args else '')))
async def list_venv_packages(venv_id):
    """按 venv_id 列出依赖包（支持 public_id 和内部 id）。"""
    try:
        # 支持 public_id 和内部 id
        try:
            internal_id = int(venv_id)
            venv = await Venv.get(id=internal_id)
        except (ValueError, TypeError):
            venv = await Venv.get(public_id=str(venv_id))

        items = await project_venv_service.list_packages(venv.venv_path)
        return {"venv_id": venv.public_id, "packages": items}
    except Exception as e:
        logger.error(f"获取依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config", response_model=EnvConfigResponse)
@fast_response(cache_ttl=300, namespace="envs:config")
async def get_env_config():
    """返回环境相关的存储配置。"""
    return EnvConfigResponse(venv_root=settings.VENV_STORAGE_ROOT, mise_root=settings.MISE_DATA_ROOT)


@router.post("/venvs", status_code=status.HTTP_201_CREATED)
async def create_shared_venv(req, current_user_id=Depends(get_current_user_id)):
    """创建或复用共享虚拟环境。"""
    try:
        info = await project_venv_service.create_or_use_shared(
            req.version,
            req.shared_venv_key,
            created_by=current_user_id,
            interpreter_source=req.interpreter_source,
            python_bin=req.python_bin,
        )
        return info
    except Exception as e:
        logger.error(f"创建共享环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/venvs/{venv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_venv(venv_id, allow_private=Query(False), current_user=Depends(get_current_user)):
    try:
        # 支持 public_id 和内部 id
        try:
            internal_id = int(venv_id)
            venv = await Venv.get(id=internal_id)
        except (ValueError, TypeError):
            venv = await Venv.get(public_id=str(venv_id))
        # 权限：管理员或创建者
        creator = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(creator and getattr(creator, 'is_admin', False))

        if venv.scope == "shared":
            if not (is_admin or venv.created_by == current_user.user_id):
                raise HTTPException(status_code=403, detail="无权删除该共享环境")
            await project_venv_service.delete_shared_by_id(venv.id)
            return
        # private
        if not allow_private:
            raise HTTPException(status_code=400, detail="不允许删除私有环境（需要 allow_private=true）")
        # 找当前绑定项目 - 使用 first() 避免多记录异常
        bind = await ProjectVenvBinding.filter(venv_id=venv_id, is_current=True).first()
        if not bind:
            # 无绑定也可删除物理目录与记录（防御）
            await project_venv_service._safe_rmtree(venv.venv_path)
            await venv.delete()
            return
        # 删除项目私有环境
        from src.models import Project
        project = await Project.get(id=bind.project_id)
        if not (is_admin or project.user_id == current_user.user_id):
            raise HTTPException(status_code=403, detail="无权删除该项目的私有环境")
        await project_venv_service.delete(str(bind.project_id))
        # 清理绑定及项目字段
        await ProjectVenvBinding.filter(project_id=bind.project_id).delete()
        project.current_venv_id = None
        project.venv_path = None
        project.python_version = None
        project.venv_scope = None
        await project.save()
        # 删除 venv 记录
        try:
            await venv.delete()
        except Exception:
            pass
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/venvs/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_venvs(body, current_user_id=Depends(get_current_user_id)):
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids 必须为非空数组")
    success = 0
    failed: list[str] = []
    for vid in ids:
        try:
            # 支持 public_id 和内部 id
            try:
                internal_id = int(vid)
                venv = await Venv.get(id=internal_id)
            except (ValueError, TypeError):
                venv = await Venv.get(public_id=str(vid))

            await project_venv_service.delete_shared_by_id(venv.id)
            success += 1
        except Exception:
            failed.append(str(vid))
    return {"total": len(ids), "deleted": success, "failed": failed}


class InstallPackagesRequest:
    packages: list[str]


@router.post("/venvs/{venv_id}/packages", status_code=status.HTTP_200_OK)
async def install_packages_to_venv(venv_id, body, current_user_id=Depends(get_current_user_id)):
    """向指定 venv 安装依赖（支持 public_id 和内部 id）。"""
    try:
        packages = body.get("packages") or []
        if not isinstance(packages, list) or not packages:
            raise HTTPException(status_code=400, detail="packages 必须为非空列表")
        # 支持 public_id 和内部 id
        try:
            internal_id = int(venv_id)
            venv = await Venv.get(id=internal_id)
        except (ValueError, TypeError):
            venv = await Venv.get(public_id=str(venv_id))

        await project_venv_service.install_dependencies(venv.venv_path, packages)
        return {"venv_id": venv.public_id, "installed": packages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/venv/packages", status_code=status.HTTP_200_OK)
async def install_packages_to_project_venv(project_id, body, current_user_id=Depends(get_current_user_id)):
    """向项目当前绑定的 venv 安装依赖（支持 public_id 和内部 id）。"""
    try:
        packages = body.get("packages") or []
        if not isinstance(packages, list) or not packages:
            raise HTTPException(status_code=400, detail="packages 必须为非空列表")
        # 支持 public_id 和内部 id
        try:
            internal_id = int(project_id)
            project = await Project.get(id=internal_id)
        except (ValueError, TypeError):
            project = await Project.get(public_id=str(project_id))

        if not project.venv_path:
            raise HTTPException(status_code=404, detail="项目未绑定虚拟环境")
        await project_venv_service.install_dependencies(project.venv_path, packages)
        return {"project_id": project.public_id, "installed": packages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
@router.patch("/venvs/{venv_id}")
async def update_shared_venv(venv_id, body, current_user=Depends(get_current_user)):
    """编辑共享环境（目前支持修改 key，支持 public_id 和内部 id）。"""
    try:
        # 支持 public_id 和内部 id
        try:
            internal_id = int(venv_id)
            venv = await Venv.get(id=internal_id)
        except (ValueError, TypeError):
            venv = await Venv.get(public_id=str(venv_id))

        if venv.scope != "shared":
            raise HTTPException(status_code=400, detail="仅共享环境支持编辑")
        creator = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(creator and getattr(creator, 'is_admin', False))
        if not (is_admin or venv.created_by == current_user.user_id):
            raise HTTPException(status_code=403, detail="无权编辑该共享环境")
        key = body.get("key")
        if key is not None:
            venv.key = key
            await venv.save()
        return {"id": venv.public_id, "key": venv.key}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新共享环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))
