"""项目管理路由 - 与主控 API 风格保持一致"""

from typing import Optional, List
from fastapi import APIRouter, UploadFile, File, Form, Query, HTTPException, status
from pydantic import BaseModel
from loguru import logger

from ..schemas import CreateCodeProjectRequest, UpdateProjectRequest, WriteFileRequest, SyncFromMasterRequest
from ...services import local_project_service
from ...config import get_node_config

router = APIRouter(prefix="/projects", tags=["项目管理"])


# ============ 响应模型 ============

class ProjectInfo(BaseModel):
    """项目信息"""
    id: str
    name: str
    type: str
    language: Optional[str] = None
    description: Optional[str] = None
    entry_point: Optional[str] = None
    env_name: Optional[str] = None
    path: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProjectListResponse(BaseModel):
    """项目列表响应"""
    projects: List[dict]
    total: int


class FileInfo(BaseModel):
    """文件信息"""
    name: str
    path: str
    size: int
    is_dir: bool


class FileListResponse(BaseModel):
    """文件列表响应"""
    files: List[FileInfo]
    total: int


class FileContentResponse(BaseModel):
    """文件内容响应"""
    path: str
    content: str


# ============ 路由 ============

@router.get("", response_model=ProjectListResponse)
async def list_projects():
    """列出所有项目"""
    projects = await local_project_service.list_projects()
    return ProjectListResponse(projects=projects, total=len(projects))


@router.get("/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    project = await local_project_service.get_project(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在"
        )
    return project


@router.post("/code", status_code=status.HTTP_201_CREATED)
async def create_code_project(request: CreateCodeProjectRequest):
    """创建代码项目"""
    try:
        project = await local_project_service.create_code_project(
            name=request.name,
            code_content=request.code_content,
            language=request.language,
            description=request.description,
            entry_point=request.entry_point,
            env_name=request.env_name,
            master_project_id=request.master_project_id,
        )
        return project
    except Exception as e:
        logger.error(f"创建代码项目失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/sync-from-master", status_code=status.HTTP_200_OK)
async def sync_from_master(request: SyncFromMasterRequest):
    """从主节点同步项目（智能拉取）"""
    try:
        # 优先使用请求中的 access_token，否则使用节点配置的 access_token
        config = get_node_config()
        access_token = request.access_token or config.access_token
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少访问令牌"
            )

        project = await local_project_service.sync_from_master(
            master_project_id=request.project_id,
            project_name=request.name,
            download_url=request.download_url,
            access_token=access_token,
            description=request.description,
            entry_point=request.entry_point,
            transfer_method=request.transfer_method,
            file_hash=request.file_hash,
            file_size=request.file_size,
        )
        return project
    except Exception as e:
        logger.error(f"从主节点同步项目失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/file", status_code=status.HTTP_201_CREATED)
async def create_file_project(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    entry_point: Optional[str] = Form(None),
    env_name: Optional[str] = Form(None),
):
    """创建文件项目"""
    try:
        content = await file.read()
        project = await local_project_service.create_file_project(
            name=name,
            file_content=content,
            original_name=file.filename,
            description=description,
            entry_point=entry_point,
            env_name=env_name,
        )
        return project
    except Exception as e:
        logger.error(f"创建文件项目失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put("/{project_id}")
async def update_project(project_id: str, request: UpdateProjectRequest):
    """更新项目"""
    project = await local_project_service.update_project(
        project_id=project_id,
        **request.dict(exclude_unset=True)
    )
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在"
        )
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str):
    """删除项目"""
    deleted = await local_project_service.delete_project(project_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目 {project_id} 不存在"
        )
    return None


@router.get("/{project_id}/files", response_model=FileListResponse)
async def get_project_files(project_id: str):
    """获取项目文件列表"""
    files = await local_project_service.get_project_files(project_id)
    return FileListResponse(files=files, total=len(files))


@router.get("/{project_id}/files/content", response_model=FileContentResponse)
async def read_project_file(project_id: str, file_path: str = Query(...)):
    """读取文件内容"""
    try:
        content = await local_project_service.read_project_file(project_id, file_path)
        if content is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文件不存在"
            )
        return FileContentResponse(path=file_path, content=content)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{project_id}/files/content")
async def write_project_file(project_id: str, request: WriteFileRequest):
    """写入文件"""
    try:
        success = await local_project_service.write_project_file(
            project_id, request.file_path, request.content
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="写入失败"
            )
        return {"path": request.file_path, "saved": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"写入文件失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{project_id}/bind-env")
async def bind_env_to_project(project_id: str, env_name: str = Query(...)):
    """绑定环境到项目"""
    success = await local_project_service.bind_env(project_id, env_name)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="绑定失败"
        )
    return {"project_id": project_id, "env_name": env_name, "bound": True}


@router.post("/{project_id}/unbind-env")
async def unbind_env_from_project(project_id: str):
    """解绑项目环境"""
    success = await local_project_service.unbind_env(project_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="解绑失败"
        )
    return {"project_id": project_id, "unbound": True}
