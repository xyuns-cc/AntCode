"""环境管理接口

Python 解释器和运行时管理 API。
"""

from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel
from tortoise.expressions import Q

from antcode_core.common.config import settings
from antcode_web_api.response import page as page_response
from antcode_core.common.security.auth import TokenData, get_current_user
from antcode_core.domain.models import Project, User
from antcode_core.domain.models.enums import InterpreterSource, RuntimeKind, RuntimeScope
from antcode_core.domain.models.runtime import Interpreter, ProjectRuntimeBinding, Runtime
from antcode_core.domain.schemas.runtime import (
    InterpreterInfo,
    PythonVersionListResponse,
    RuntimeListItem,
    RuntimeStatusResponse,
)

router = APIRouter(tags=["环境管理"])

# 安装锁
_install_locks: dict[str, asyncio.Lock] = {}
_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _lock_for(version: str) -> asyncio.Lock:
    key = f"python@{version}"
    if key not in _install_locks:
        _install_locks[key] = asyncio.Lock()
    return _install_locks[key]


def _normalize_path_component(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} 不能为空")
    if "/" in normalized or "\\" in normalized or ".." in normalized or "\x00" in normalized:
        raise HTTPException(status_code=400, detail=f"{field_name} 包含非法路径字符")
    if not _SAFE_PATH_COMPONENT_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 仅允许字母、数字、点、下划线和短横线",
        )
    return normalized


def _safe_join_runtime_path(base_dir: str, component: str, field_name: str) -> str:
    safe_component = _normalize_path_component(component, field_name)
    base_abs = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_abs, safe_component))
    if os.path.commonpath([base_abs, candidate]) != base_abs:
        raise HTTPException(status_code=400, detail=f"{field_name} 非法")
    return candidate


async def _get_current_user_or_401(current_user: TokenData) -> User:
    user = await User.get_or_none(id=current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或会话已失效")
    return user


async def _ensure_admin_user(current_user: TokenData) -> User:
    user = await _get_current_user_or_401(current_user)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


async def _resolve_project(project_id: str) -> Project:
    try:
        internal_id = int(project_id)
        project = await Project.get_or_none(id=internal_id)
    except (ValueError, TypeError):
        project = await Project.get_or_none(public_id=str(project_id))

    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


async def _ensure_project_access(project_id: str, current_user: TokenData) -> Project:
    project = await _resolve_project(project_id)
    user = await _get_current_user_or_401(current_user)
    if not user.is_admin and project.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该项目")
    return project


async def _resolve_runtime(runtime_id: str) -> Runtime:
    try:
        internal_id = int(runtime_id)
        runtime = await Runtime.get_or_none(id=internal_id)
    except (ValueError, TypeError):
        runtime = await Runtime.get_or_none(public_id=str(runtime_id))

    if not runtime:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="运行时不存在")
    return runtime


async def _ensure_runtime_access(runtime: Runtime, current_user: TokenData) -> None:
    user = await _get_current_user_or_401(current_user)
    if user.is_admin:
        return

    runtime_scope = runtime.scope.value if hasattr(runtime.scope, "value") else str(runtime.scope)
    if runtime_scope == RuntimeScope.SHARED.value:
        if runtime.created_by == current_user.user_id:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该共享环境")

    bindings = await ProjectRuntimeBinding.filter(runtime_id=runtime.id, is_current=True).all()
    if not bindings:
        if runtime.created_by == current_user.user_id:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该私有环境")

    project_ids = [binding.project_id for binding in bindings]
    has_owned_project = await Project.filter(
        id__in=project_ids,
        user_id=current_user.user_id,
    ).exists()
    if has_owned_project:
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该私有环境")


class EnvConfigResponse(BaseModel):
    runtime_root: str
    mise_root: str


# ==================== Python 版本和解释器管理 ====================


@router.get("/python/versions", response_model=PythonVersionListResponse)
async def list_python_versions(current_user: TokenData = Depends(get_current_user)):
    """列出 mise 支持的 Python 版本。"""
    await _ensure_admin_user(current_user)
    try:
        from antcode_core.common.command_runner import run_command

        res = await run_command(["mise", "ls-remote", "python"], timeout=120)
        if res.exit_code != 0:
            logger.warning(f"mise 列表命令失败: {res.stderr.strip()}")
            return PythonVersionListResponse(versions=[])
        versions = []
        for line in res.stdout.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            ver = parts[0]
            versions.append(ver)
        return PythonVersionListResponse(versions=versions)
    except FileNotFoundError:
        logger.warning("未检测到 mise，返回空版本列表")
        return PythonVersionListResponse(versions=[])
    except Exception as e:
        if "No such file or directory" in str(e) and "mise" in str(e):
            logger.warning("未检测到 mise，返回空版本列表")
            return PythonVersionListResponse(versions=[])
        logger.error(f"获取版本失败: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="获取版本失败")


@router.get("/python/interpreters", response_model=list[InterpreterInfo])
async def list_python_interpreters(
    source: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
):
    """列出已注册的 Python 解释器。"""
    await _ensure_admin_user(current_user)
    try:
        query = Interpreter.filter(tool="python")
        if source:
            query = query.filter(source=source)
        rows = await query.all()
        return [
            InterpreterInfo(
                id=r.public_id,
                version=r.version,
                install_dir=r.install_dir,
                python_bin=r.python_bin,
                source=r.source.value if hasattr(r.source, "value") else str(r.source),
            )
            for r in rows
        ]
    except Exception as e:
        logger.error(f"获取已安装解释器失败: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.post("/python/interpreters", response_model=InterpreterInfo, status_code=status.HTTP_201_CREATED)
async def install_python_interpreter(
    version: str = Body(..., embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """安装指定 Python 版本（幂等）。"""
    try:
        from antcode_core.common.command_runner import run_command

        lock = _lock_for(version)
        async with lock:
            res = await run_command(["mise", "install", "-y", f"python@{version}"], timeout=1800)
            if res.exit_code != 0:
                logger.warning(f"mise install 返回非零: {res.exit_code} -> {res.stderr.strip()}")

            where = await run_command(["mise", "where", f"python@{version}"], timeout=60)
            if where.exit_code != 0:
                raise RuntimeError(f"查找解释器失败: {where.stderr.strip()}")
            install_dir = where.stdout.strip()
            candidates = [
                os.path.join(install_dir, "bin", "python"),
                os.path.join(install_dir, "bin", "python3"),
            ]
            python_bin = next((p for p in candidates if os.path.exists(p)), candidates[0])

            # upsert interpreter record
            obj = await Interpreter.get_or_none(tool="python", version=version, source=InterpreterSource.MISE)
            if obj:
                obj.install_dir = install_dir
                obj.python_bin = python_bin
                await obj.save()
            else:
                obj = await Interpreter.create(
                    tool="python",
                    version=version,
                    install_dir=install_dir,
                    python_bin=python_bin,
                    status="installed",
                    source=InterpreterSource.MISE,
                    created_by=current_user.user_id,
                )
            return InterpreterInfo(
                id=obj.public_id,
                version=version,
                install_dir=install_dir,
                python_bin=python_bin,
                source="mise",
            )
    except Exception as e:
        logger.error(f"安装解释器失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/python/interpreters/local", response_model=InterpreterInfo, status_code=status.HTTP_201_CREATED)
async def register_local_interpreter(
    python_bin: str = Body(..., embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """注册本地 Python 解释器。"""
    try:
        from antcode_core.common.command_runner import run_command

        if not os.path.exists(python_bin):
            raise RuntimeError("python_bin 路径不存在")
        res = await run_command([python_bin, "--version"], timeout=30)
        if res.exit_code != 0:
            raise RuntimeError(f"读取版本失败: {res.stderr or res.stdout}")
        ver = res.stdout.strip().split()[-1]
        install_dir = os.path.dirname(python_bin)

        obj = await Interpreter.get_or_none(tool="python", version=ver, source=InterpreterSource.LOCAL)
        if obj:
            obj.install_dir = install_dir
            obj.python_bin = python_bin
            await obj.save()
        else:
            obj = await Interpreter.create(
                tool="python",
                version=ver,
                install_dir=install_dir,
                python_bin=python_bin,
                status="installed",
                source=InterpreterSource.LOCAL,
                created_by=current_user.user_id,
            )
        return InterpreterInfo(
            id=obj.public_id, version=ver, install_dir=install_dir, python_bin=python_bin, source="local"
        )
    except Exception as e:
        logger.error(f"注册本地解释器失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/python/interpreters/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_python_interpreter(
    version: str,
    source: str = Query("mise"),
    current_user: TokenData = Depends(get_current_user),
):
    """卸载或删除解释器。"""
    await _ensure_admin_user(current_user)
    try:
        if source in ("local", "system"):
            deleted = await Interpreter.filter(tool="python", version=version, source=source).delete()
            if deleted == 0:
                raise HTTPException(status_code=404, detail=f"未找到 {source} 解释器记录")
            return
        from antcode_core.common.command_runner import run_command

        await run_command(["mise", "uninstall", f"python@{version}"])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"卸载解释器失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ==================== 运行时管理 ====================


@router.get("")
async def list_runtimes(
    scope: str = None,
    project_id: int = None,
    q: str = None,
    page: int = 1,
    size: int = 20,
    include_packages: bool = False,
    limit_packages: int = 50,
    interpreter_source: str = None,
    worker_id: str = Query(None, description="Worker ID 筛选"),
    current_user: TokenData = Depends(get_current_user),
):
    """分页列出运行时。"""
    from antcode_core.domain.models import Worker

    try:
        query = Runtime.all()
        if scope is not None:
            query = query.filter(scope=scope)
        if project_id is not None:
            runtime_ids = await ProjectRuntimeBinding.filter(
                project_id=project_id, is_current=True
            ).values_list("runtime_id", flat=True)
            query = query.filter(id__in=list(runtime_ids))
        if q:
            query = query.filter(Q(runtime_locator__icontains=q) | Q(key__icontains=q) | Q(version__icontains=q))

        # 节点筛选
        if worker_id:
            worker = await Worker.filter(public_id=worker_id).first()
            if worker:
                query = query.filter(worker_id=worker.id)
            else:
                try:
                    query = query.filter(worker_id=int(worker_id))
                except ValueError:
                    pass

        # 权限控制
        user = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(user and getattr(user, "is_admin", False))
        if not is_admin:
            created_filter = Q(created_by=current_user.user_id)
            my_project_ids = await Project.filter(user_id=current_user.user_id).values_list("id", flat=True)
            my_runtime_ids = []
            if my_project_ids:
                my_runtime_ids = await ProjectRuntimeBinding.filter(
                    project_id__in=list(my_project_ids), is_current=True
                ).values_list("runtime_id", flat=True)
            if my_runtime_ids:
                query = query.filter(created_filter | Q(id__in=list(my_runtime_ids)))
            else:
                query = query.filter(created_filter)

        total = await query.count()
        items = await query.offset((page - 1) * size).limit(size)

        # 预取创建者用户名
        creator_ids = {v.created_by for v in items if getattr(v, "created_by", None)}
        user_map = {}
        if creator_ids:
            users = await User.filter(id__in=list(creator_ids)).values("id", "username")
            user_map = {u["id"]: u["username"] for u in users}

        data: list[RuntimeListItem] = []
        for v in items:
            interpreter = await Interpreter.get_or_none(id=v.interpreter_id)
            current_binding = await ProjectRuntimeBinding.filter(runtime_id=v.id, is_current=True).first()
            current_project_public_id = ""
            if current_binding:
                bound_project = await Project.get_or_none(id=current_binding.project_id)
                current_project_public_id = bound_project.public_id if bound_project else ""

            created_by_public_id = ""
            if getattr(v, "created_by", None):
                creator = await User.get_or_none(id=v.created_by)
                created_by_public_id = creator.public_id if creator else ""

            item = RuntimeListItem(
                id=v.public_id,
                runtime_kind=v.runtime_kind,
                scope=v.scope,
                key=v.key or "",
                version=v.version,
                runtime_locator=v.runtime_locator,
                runtime_details=v.runtime_details or {},
                interpreter_version=interpreter.version if interpreter else "",
                interpreter_source=interpreter.source.value if interpreter and hasattr(interpreter.source, "value") else "",
                python_bin=interpreter.python_bin if interpreter else "",
                install_dir=interpreter.install_dir if interpreter else "",
                created_by=created_by_public_id,
                created_by_username=user_map.get(v.created_by, "") if getattr(v, "created_by", None) else "",
                created_at=v.created_at.isoformat() if v.created_at else "",
                updated_at=v.updated_at.isoformat() if v.updated_at else "",
                current_project_id=current_project_public_id,
            )
            data.append(item)

        return page_response(items=data, total=total, page=page, size=size)
    except Exception as e:
        logger.error(f"列出运行时失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{runtime_id}/packages")
async def list_runtime_packages(
    runtime_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """按 runtime_id 列出依赖包。"""
    try:
        from antcode_core.common.command_runner import run_command

        runtime = await _resolve_runtime(runtime_id)
        await _ensure_runtime_access(runtime, current_user)

        py = _runtime_python(runtime.runtime_locator)
        res = await run_command(["uv", "pip", "list", "--format", "json", "--python", py], timeout=120)
        if res.exit_code != 0 or not res.stdout.strip():
            res = await run_command([py, "-m", "pip", "list", "--format", "json"], timeout=120)
        if res.exit_code != 0:
            raise RuntimeError(f"获取依赖列表失败: {res.stderr.strip()}")
        import json

        packages = json.loads(res.stdout) if res.stdout.strip() else []
        return {"runtime_id": runtime.public_id, "packages": packages}
    except Exception as e:
        logger.error(f"获取依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


def _runtime_python(runtime_dir: str) -> str:
    """获取运行时的 Python 可执行文件路径。"""
    candidates = [
        os.path.join(runtime_dir, "bin", "python"),
        os.path.join(runtime_dir, "bin", "python3"),
        os.path.join(runtime_dir, "Scripts", "python.exe"),
    ]
    return next((p for p in candidates if os.path.exists(p)), candidates[0])


@router.post("/{runtime_id}/packages", status_code=status.HTTP_200_OK)
async def install_packages_to_runtime(
    runtime_id: str,
    packages: list[str] = Body(..., embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """向指定 runtime 安装依赖。"""
    try:
        from antcode_core.common.command_runner import run_command

        if not packages:
            raise HTTPException(status_code=400, detail="packages 必须为非空列表")
        runtime = await _resolve_runtime(runtime_id)
        await _ensure_runtime_access(runtime, current_user)

        py = _runtime_python(runtime.runtime_locator)
        args = ["uv", "pip", "install", "-q", "--python", py] + packages
        res = await run_command(args, timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"安装依赖失败: {res.stderr.strip() or res.stdout.strip()}")
        return {"runtime_id": runtime.public_id, "installed": packages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_shared_runtime(
    version: str = Body(...),
    shared_runtime_key: str = Body(None),
    interpreter_source: str = Body("mise"),
    python_bin: str = Body(None),
    current_user: TokenData = Depends(get_current_user),
):
    """创建或复用共享运行时。"""
    await _ensure_admin_user(current_user)
    try:
        from antcode_core.common.command_runner import run_command

        version = _normalize_path_component(version, "version")
        ident = _normalize_path_component(shared_runtime_key or version, "shared_runtime_key")
        runtime_root = os.path.join(settings.VENV_STORAGE_ROOT, "shared")
        runtime_dir = _safe_join_runtime_path(runtime_root, ident, "shared_runtime_key")

        os.makedirs(runtime_root, exist_ok=True)

        # 确保解释器存在
        if interpreter_source == "local":
            if not python_bin:
                raise RuntimeError("本地解释器需要提供 python_bin")
            if not os.path.exists(python_bin):
                raise RuntimeError("python_bin 路径不存在")
            res = await run_command([python_bin, "--version"], timeout=30)
            ver = res.stdout.strip().split()[-1]
            install_dir = os.path.dirname(python_bin)
            obj = await Interpreter.get_or_none(tool="python", version=ver, source=InterpreterSource.LOCAL)
            if not obj:
                obj = await Interpreter.create(
                    tool="python", version=ver, install_dir=install_dir, python_bin=python_bin,
                    status="installed", source=InterpreterSource.LOCAL, created_by=current_user.user_id,
                )
            python_exe = python_bin
        else:
            lock = _lock_for(version)
            async with lock:
                await run_command(["mise", "install", "-y", f"python@{version}"], timeout=1800)
                where = await run_command(["mise", "where", f"python@{version}"], timeout=60)
                if where.exit_code != 0:
                    raise RuntimeError(f"查找解释器失败: {where.stderr.strip()}")
                install_dir = where.stdout.strip()
                candidates = [os.path.join(install_dir, "bin", "python"), os.path.join(install_dir, "bin", "python3")]
                python_exe = next((p for p in candidates if os.path.exists(p)), candidates[0])
                obj = await Interpreter.get_or_none(tool="python", version=version, source=InterpreterSource.MISE)
                if not obj:
                    obj = await Interpreter.create(
                        tool="python", version=version, install_dir=install_dir, python_bin=python_exe,
                        status="installed", source=InterpreterSource.MISE, created_by=current_user.user_id,
                    )

        if not os.path.exists(runtime_dir):
            os.makedirs(runtime_dir, exist_ok=True)
            res = await run_command(["uv", "venv", runtime_dir, "--python", python_exe], timeout=900)
            if res.exit_code != 0:
                raise RuntimeError(f"创建共享运行时失败: {res.stderr.strip()}")

        interpreter = await Interpreter.filter(tool="python", version=version).first()
        if not interpreter:
            raise RuntimeError(f"未找到 Python {version} 解释器")
        runtime_obj = await Runtime.get_or_none(runtime_locator=runtime_dir)
        if not runtime_obj:
            runtime_obj = await Runtime.create(
                runtime_kind=RuntimeKind.PYTHON, scope=RuntimeScope.SHARED, key=ident, version=version, runtime_locator=runtime_dir, runtime_details={"python_version": version},
                interpreter_id=interpreter.id, created_by=current_user.user_id,
            )

        return {"version": version, "runtime_locator": runtime_dir, "key": ident, "runtime_id": runtime_obj.public_id}
    except Exception as e:
        logger.error(f"创建共享环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime(
    runtime_id: str,
    allow_private: bool = Query(False),
    current_user: TokenData = Depends(get_current_user),
):
    """删除运行时。"""
    try:
        try:
            internal_id = int(runtime_id)
            runtime = await Runtime.get(id=internal_id)
        except (ValueError, TypeError):
            runtime = await Runtime.get(public_id=str(runtime_id))

        user = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(user and getattr(user, "is_admin", False))

        if runtime.scope == RuntimeScope.SHARED or runtime.scope == "shared":
            if not (is_admin or runtime.created_by == current_user.user_id):
                raise HTTPException(status_code=403, detail="无权删除该共享环境")
            # 检查是否有绑定
            bindings = await ProjectRuntimeBinding.filter(runtime_id=runtime.id).all()
            if bindings:
                project_ids = [b.project_id for b in bindings]
                projects = await Project.filter(id__in=project_ids).all()
                project_names = [p.name for p in projects]
                raise HTTPException(status_code=400, detail=f"该运行时正在被以下项目使用: {', '.join(project_names)}")
            await _safe_rmtree(runtime.runtime_locator)
            await runtime.delete()
            return

        # private
        if not allow_private:
            raise HTTPException(status_code=400, detail="不允许删除私有环境（需要 allow_private=true）")
        bind = await ProjectRuntimeBinding.filter(runtime_id=runtime.id, is_current=True).first()
        if not bind:
            await _safe_rmtree(runtime.runtime_locator)
            await runtime.delete()
            return
        project = await Project.get(id=bind.project_id)
        if not (is_admin or project.user_id == current_user.user_id):
            raise HTTPException(status_code=403, detail="无权删除该项目的私有环境")
        await _safe_rmtree(runtime.runtime_locator)
        await ProjectRuntimeBinding.filter(project_id=bind.project_id).delete()
        project.current_runtime_id = None
        project.runtime_locator = None
        project.python_version = None
        project.runtime_scope = None
        project.runtime_kind = None
        await project.save()
        try:
            await runtime.delete()
        except Exception:
            pass
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


async def _safe_rmtree(path: str | None) -> None:
    """安全删除目录。"""
    if not path or not os.path.exists(path):
        return
    runtime_root = settings.VENV_STORAGE_ROOT
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(runtime_root) + os.sep):
        logger.warning(f"拒绝删除非 runtime 路径: {abs_path}")
        return
    for root, dirs, files in os.walk(abs_path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except FileNotFoundError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(abs_path)
    except OSError:
        pass


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_runtimes(
    ids: list[str] = Body(..., embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """批量删除运行时。"""
    await _ensure_admin_user(current_user)
    if not ids:
        raise HTTPException(status_code=400, detail="ids 必须为非空数组")
    success_count = 0
    failed: list[str] = []
    for vid in ids:
        try:
            runtime = await _resolve_runtime(str(vid))
            bindings = await ProjectRuntimeBinding.filter(runtime_id=runtime.id).all()
            if bindings:
                failed.append(str(vid))
                continue
            await _safe_rmtree(runtime.runtime_locator)
            await runtime.delete()
            success_count += 1
        except Exception:
            failed.append(str(vid))
    return {"total": len(ids), "deleted": success_count, "failed": failed}


@router.patch("/{runtime_id}")
async def update_shared_runtime(
    runtime_id: str,
    key: str = Body(None, embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """编辑共享环境。"""
    try:
        try:
            internal_id = int(runtime_id)
            runtime = await Runtime.get(id=internal_id)
        except (ValueError, TypeError):
            runtime = await Runtime.get(public_id=str(runtime_id))

        if runtime.scope != RuntimeScope.SHARED and runtime.scope != "shared":
            raise HTTPException(status_code=400, detail="仅共享环境支持编辑")
        user = await User.get_or_none(id=current_user.user_id)
        is_admin = bool(user and getattr(user, "is_admin", False))
        if not (is_admin or runtime.created_by == current_user.user_id):
            raise HTTPException(status_code=403, detail="无权编辑该共享环境")
        if key is not None:
            runtime.key = key
            await runtime.save()
        return {"id": runtime.public_id, "key": runtime.key}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新共享环境失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ==================== 项目运行时管理 ====================


@router.get("/projects/{project_id}/runtime", response_model=RuntimeStatusResponse)
async def get_project_runtime(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """获取项目运行时状态。"""
    try:
        project = await _ensure_project_access(project_id, current_user)

        return RuntimeStatusResponse(
            project_id=project.public_id,
            runtime_kind=project.runtime_kind or RuntimeKind.PYTHON,
            scope=project.runtime_scope.value if project.runtime_scope and hasattr(project.runtime_scope, "value") else (project.runtime_scope or ""),
            version=project.python_version or "",
            runtime_locator=project.runtime_locator or "",
        )
    except Exception as e:
        logger.error(f"获取 runtime 状态失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/projects/{project_id}/runtime", response_model=RuntimeStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_or_bind_project_runtime(
    project_id: str,
    version: str = Body(...),
    runtime_scope: str = Body(...),
    runtime_kind: RuntimeKind = Body(RuntimeKind.PYTHON),
    shared_runtime_key: str = Body(None),
    create_if_missing: bool = Body(True),
    interpreter_source: str = Body("mise"),
    python_bin: str = Body(None),
    current_user: TokenData = Depends(get_current_user),
):
    """创建/绑定项目运行时。"""
    try:
        from antcode_core.common.command_runner import run_command

        project = await _ensure_project_access(project_id, current_user)
        version = _normalize_path_component(version, "version")
        if shared_runtime_key:
            shared_runtime_key = _normalize_path_component(shared_runtime_key, "shared_runtime_key")

        scope = RuntimeScope.SHARED if runtime_scope == "shared" else RuntimeScope.PRIVATE
        if runtime_kind != RuntimeKind.PYTHON:
            raise HTTPException(status_code=400, detail="当前仅支持 python 运行时")

        if scope == RuntimeScope.PRIVATE:
            # 私有环境
            runtime_root = os.path.join(settings.VENV_STORAGE_ROOT, str(project.id))
            runtime_dir = _safe_join_runtime_path(runtime_root, version, "version")
            os.makedirs(runtime_root, exist_ok=True)

            if interpreter_source == "local":
                if not python_bin:
                    raise RuntimeError("本地解释器需要提供 python_bin")
                python_exe = python_bin
            else:
                lock = _lock_for(version)
                async with lock:
                    await run_command(["mise", "install", "-y", f"python@{version}"], timeout=1800)
                    where = await run_command(["mise", "where", f"python@{version}"], timeout=60)
                    if where.exit_code != 0:
                        raise RuntimeError(f"查找解释器失败: {where.stderr.strip()}")
                    install_dir = where.stdout.strip()
                    candidates = [os.path.join(install_dir, "bin", "python"), os.path.join(install_dir, "bin", "python3")]
                    python_exe = next((p for p in candidates if os.path.exists(p)), candidates[0])

            if not os.path.exists(runtime_dir):
                if not create_if_missing:
                    raise RuntimeError("运行时不存在且不允许创建")
                res = await run_command(["uv", "venv", runtime_dir, "--python", python_exe], timeout=900)
                if res.exit_code != 0:
                    raise RuntimeError(f"创建运行时失败: {res.stderr.strip()}")

            runtime_locator = runtime_dir
        else:
            # 共享环境
            ident = shared_runtime_key or version
            runtime_root = os.path.join(settings.VENV_STORAGE_ROOT, "shared")
            runtime_dir = _safe_join_runtime_path(runtime_root, ident, "shared_runtime_key")
            os.makedirs(runtime_root, exist_ok=True)

            if interpreter_source == "local":
                if not python_bin:
                    raise RuntimeError("本地解释器需要提供 python_bin")
                python_exe = python_bin
            else:
                lock = _lock_for(version)
                async with lock:
                    await run_command(["mise", "install", "-y", f"python@{version}"], timeout=1800)
                    where = await run_command(["mise", "where", f"python@{version}"], timeout=60)
                    if where.exit_code != 0:
                        raise RuntimeError(f"查找解释器失败: {where.stderr.strip()}")
                    install_dir = where.stdout.strip()
                    candidates = [os.path.join(install_dir, "bin", "python"), os.path.join(install_dir, "bin", "python3")]
                    python_exe = next((p for p in candidates if os.path.exists(p)), candidates[0])

            if not os.path.exists(runtime_dir):
                os.makedirs(runtime_dir, exist_ok=True)
                res = await run_command(["uv", "venv", runtime_dir, "--python", python_exe], timeout=900)
                if res.exit_code != 0:
                    raise RuntimeError(f"创建共享运行时失败: {res.stderr.strip()}")

            runtime_locator = runtime_dir

        # 记录/更新 Runtime 与绑定
        interpreter = await Interpreter.filter(tool="python", version=version).first()
        if not interpreter:
            raise RuntimeError(f"未找到 Python {version} 解释器")
        runtime_obj = await Runtime.get_or_none(runtime_locator=runtime_locator)
        if not runtime_obj:
            runtime_obj = await Runtime.create(
                runtime_kind=RuntimeKind.PYTHON, scope=scope, key=shared_runtime_key if scope == RuntimeScope.SHARED else None,
                version=version, runtime_locator=runtime_locator, runtime_details={"python_version": version}, interpreter_id=interpreter.id,
                created_by=current_user.user_id,
            )

        # 更新项目绑定
        await ProjectRuntimeBinding.filter(project_id=project.id, is_current=True).update(is_current=False)
        await ProjectRuntimeBinding.create(
            project_id=project.id, runtime_id=runtime_obj.id, is_current=True, created_by=current_user.user_id
        )

        # 更新项目字段
        project.python_version = version
        project.runtime_scope = scope
        project.runtime_kind = RuntimeKind.PYTHON
        project.runtime_locator = runtime_locator
        project.current_runtime_id = runtime_obj.id
        await project.save()

        return RuntimeStatusResponse(project_id=project.public_id, runtime_kind=runtime_kind, scope=runtime_scope, version=version, runtime_locator=runtime_locator)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建/绑定 runtime 失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/projects/{project_id}/runtime", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_runtime(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """删除项目运行时。"""
    try:
        project = await _ensure_project_access(project_id, current_user)

        if project.runtime_locator:
            await _safe_rmtree(project.runtime_locator)

        # 清理绑定
        await ProjectRuntimeBinding.filter(project_id=project.id).delete()
        project.current_runtime_id = None
        project.runtime_locator = None
        project.python_version = None
        project.runtime_scope = None
        await project.save()
    except Exception as e:
        logger.error(f"删除 runtime 失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/projects/{project_id}/runtime/packages")
async def list_project_runtime_packages(
    project_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """列出项目运行时中的依赖包。"""
    try:
        from antcode_core.common.command_runner import run_command

        project = await _ensure_project_access(project_id, current_user)

        if not project.runtime_locator:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目未绑定运行时")

        py = _runtime_python(project.runtime_locator)
        res = await run_command(["uv", "pip", "list", "--format", "json", "--python", py], timeout=120)
        if res.exit_code != 0 or not res.stdout.strip():
            res = await run_command([py, "-m", "pip", "list", "--format", "json"], timeout=120)
        if res.exit_code != 0:
            raise RuntimeError(f"获取依赖列表失败: {res.stderr.strip()}")
        import json

        packages = json.loads(res.stdout) if res.stdout.strip() else []
        return {"project_id": project.public_id, "packages": packages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取依赖列表失败: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/projects/{project_id}/runtime/packages", status_code=status.HTTP_200_OK)
async def install_packages_to_project_runtime(
    project_id: str,
    packages: list[str] = Body(..., embed=True),
    current_user: TokenData = Depends(get_current_user),
):
    """向项目当前绑定的 runtime 安装依赖。"""
    try:
        from antcode_core.common.command_runner import run_command

        if not packages:
            raise HTTPException(status_code=400, detail="packages 必须为非空列表")
        project = await _ensure_project_access(project_id, current_user)

        if not project.runtime_locator:
            raise HTTPException(status_code=404, detail="项目未绑定运行时")

        py = _runtime_python(project.runtime_locator)
        args = ["uv", "pip", "install", "-q", "--python", py] + packages
        res = await run_command(args, timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"安装依赖失败: {res.stderr.strip() or res.stdout.strip()}")
        return {"project_id": project.public_id, "installed": packages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装依赖失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config", response_model=EnvConfigResponse)
async def get_env_config(current_user: TokenData = Depends(get_current_user)):
    """返回环境相关的存储配置。"""
    await _ensure_admin_user(current_user)
    return EnvConfigResponse(
        runtime_root=settings.VENV_STORAGE_ROOT,
        mise_root=getattr(settings, "MISE_DATA_ROOT", ""),
    )
