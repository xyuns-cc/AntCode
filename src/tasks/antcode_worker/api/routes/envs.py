"""环境管理路由 - 与主控 API 风格保持一致"""

import os
from typing import Optional, List
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Body, status
from pydantic import BaseModel
from loguru import logger
import ujson

from ..schemas import CreateEnvRequest, UpdateEnvRequest, InstallPackagesRequest, UninstallPackagesRequest
from ...services import local_env_service

router = APIRouter(prefix="/envs", tags=["环境管理"])


# ============ 响应模型 ============

class EnvInfo(BaseModel):
    """环境信息"""
    name: str
    python_version: Optional[str] = None
    path: str
    python_bin: str
    packages_count: int = 0
    key: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[str] = None
    created_by: Optional[str] = None


class EnvListResponse(BaseModel):
    """环境列表响应"""
    envs: List[EnvInfo]
    total: int


class PackageInfo(BaseModel):
    """包信息"""
    name: str
    version: str


class PackageListResponse(BaseModel):
    """包列表响应"""
    packages: List[PackageInfo]
    total: int


class InterpreterInfo(BaseModel):
    """解释器信息 - 与主控格式一致"""
    version: str
    python_bin: str
    install_dir: str = ""
    source: str = "local"
    is_available: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PythonVersionsResponse(BaseModel):
    """Python 版本响应"""
    installed: List[dict]
    available: List[str]
    all_interpreters: List[dict]
    platform: dict


class PlatformInfo(BaseModel):
    """平台信息"""
    system: str
    release: str
    machine: str
    python_version: str


# ============ 环境管理 ============

@router.get("", response_model=EnvListResponse)
async def list_envs():
    """列出所有虚拟环境"""
    envs = await local_env_service.list_envs()
    return EnvListResponse(envs=envs, total=len(envs))


@router.get("/{env_name}")
async def get_env(env_name: str):
    """获取环境详情"""
    env = await local_env_service.get_env(env_name)
    if not env:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"虚拟环境 {env_name} 不存在"
        )
    return env


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_env(request: CreateEnvRequest):
    """创建虚拟环境"""
    try:
        env = await local_env_service.create_env(
            env_name=request.name,
            python_version=request.python_version,
            packages=request.packages,
            created_by=request.created_by,
        )
        return env
    except Exception as e:
        logger.error(f"创建环境失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/{env_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_env(env_name: str):
    """删除虚拟环境"""
    deleted = await local_env_service.delete_env(env_name)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"环境 {env_name} 不存在"
        )
    return None


@router.patch("/{env_name}")
async def update_env(env_name: str, request: UpdateEnvRequest):
    """更新环境信息"""
    try:
        venv_path = local_env_service._get_venv_path(env_name)

        if not os.path.exists(venv_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"虚拟环境 {env_name} 不存在"
            )

        manifest_path = os.path.join(venv_path, "manifest.json")
        manifest = {}
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = ujson.load(f)

        if request.key is not None:
            manifest["key"] = request.key
        if request.description is not None:
            manifest["description"] = request.description

        with open(manifest_path, "w", encoding="utf-8") as f:
            ujson.dump(manifest, f, ensure_ascii=False, indent=2)

        env = await local_env_service.get_env(env_name)
        return env
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新环境失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ============ 包管理 ============

@router.get("/{env_name}/packages")
async def list_packages(env_name: str):
    """列出环境中的包"""
    try:
        packages = await local_env_service.list_packages(env_name)
        return {"packages": packages, "total": len(packages)}
    except Exception as e:
        logger.error(f"列出包失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{env_name}/packages", status_code=status.HTTP_200_OK)
async def install_packages(env_name: str, request: InstallPackagesRequest):
    """安装包到环境"""
    try:
        result = await local_env_service.install_packages(
            env_name=env_name,
            packages=request.packages,
            upgrade=request.upgrade,
        )
        return result
    except Exception as e:
        logger.error(f"安装包失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/{env_name}/packages")
async def uninstall_packages(env_name: str, request: UninstallPackagesRequest):
    """从环境卸载包"""
    try:
        result = await local_env_service.uninstall_packages(
            env_name=env_name,
            packages=request.packages,
        )
        return result
    except Exception as e:
        logger.error(f"卸载包失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ============ Python 版本管理 ============

@router.get("/python/versions", response_model=PythonVersionsResponse, tags=["Python版本"])
async def get_python_versions():
    """获取 Python 版本信息"""
    installed = await local_env_service.get_installed_python_versions()
    available = await local_env_service.get_available_python_versions()
    all_interpreters = await local_env_service.list_all_interpreters()
    platform_info = await local_env_service.get_platform_info_async()

    return PythonVersionsResponse(
        installed=installed,
        available=available,
        all_interpreters=all_interpreters,
        platform=platform_info,
    )


@router.post("/python/versions/{version}/install", tags=["Python版本"])
async def install_python_version(version: str, background_tasks: BackgroundTasks):
    """安装 Python 版本（后台任务）"""
    async def do_install():
        try:
            await local_env_service.install_python_version(version)
            logger.info(f"Python {version} 安装成功")
        except Exception as e:
            logger.error(f"Python {version} 安装失败: {e}")

    background_tasks.add_task(do_install)
    return {"message": f"Python {version} 安装任务已提交", "version": version}


# ============ 解释器管理 - 与主控 API 格式一致 ============

@router.get("/python/interpreters", response_model=List[InterpreterInfo], tags=["Python版本"])
async def list_interpreters(source: Optional[str] = Query(None, description="来源过滤")):
    """列出已注册的 Python 解释器"""
    interpreters = await local_env_service.list_all_interpreters()

    # 按来源过滤
    if source:
        interpreters = [i for i in interpreters if i.get("source") == source]

    # 转换为标准格式
    result = []
    for interp in interpreters:
        python_bin = interp.get("python_bin", "")
        # 安装目录：优先使用 install_dir，否则从 python_bin 推导
        install_dir = interp.get("install_dir", "")
        if not install_dir and python_bin:
            import os
            # 从 python 路径推导安装目录 (去掉 bin/python)
            install_dir = os.path.dirname(os.path.dirname(python_bin))
        result.append(InterpreterInfo(
            version=interp.get("version", "unknown"),
            python_bin=python_bin,
            install_dir=install_dir,
            source=interp.get("source", "local"),
            is_available=interp.get("is_available", True),
            created_at=interp.get("created_at") or interp.get("registered_at"),
            updated_at=interp.get("updated_at"),
        ))

    return result


@router.post("/python/interpreters/local", response_model=InterpreterInfo, status_code=status.HTTP_201_CREATED, tags=["Python版本"])
async def register_local_interpreter(python_bin: str = Body(..., embed=True)):
    """注册本地 Python 解释器"""
    try:
        result = await local_env_service.register_local_interpreter(python_bin)
        return InterpreterInfo(
            version=result.get("version", "unknown"),
            python_bin=result.get("python_bin", python_bin),
            source="local",
            is_available=True,
            created_at=result.get("created_at"),
            updated_at=result.get("updated_at"),
        )
    except Exception as e:
        logger.error(f"注册解释器失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/python/interpreters/{version}", status_code=status.HTTP_204_NO_CONTENT, tags=["Python版本"])
async def uninstall_interpreter(version: str, source: str = Query("local", description="来源")):
    """卸载或删除解释器"""
    try:
        # 根据版本和来源查找解释器
        interpreters = await local_env_service.list_all_interpreters()
        target = None
        for interp in interpreters:
            if interp.get("version") == version and interp.get("source", "local") == source:
                target = interp
                break

        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"解释器 {version} (source={source}) 不存在"
            )

        python_bin = target.get("python_bin")
        await local_env_service.unregister_local_interpreter(python_bin)
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"卸载解释器失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/python/interpreters", tags=["Python版本"])
async def clear_interpreters():
    """清除所有本地注册的解释器数据"""
    try:
        result = local_env_service.clear_interpreters()
        return result
    except Exception as e:
        logger.error(f"清除解释器失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# ============ 平台信息 ============

@router.get("/platform", response_model=PlatformInfo, tags=["Python版本"])
async def get_platform_info():
    """获取平台信息"""
    info = await local_env_service.get_platform_info_async()
    return PlatformInfo(**info)
