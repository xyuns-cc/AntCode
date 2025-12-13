"""
本地项目管理服务
负责项目文件的存储、管理
"""
import asyncio
import os
import shutil

import ujson
import tarfile
import zipfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any

from loguru import logger

from ..utils.hash_utils import calculate_file_hash, calculate_content_hash
from .project_cache import ProjectCache


@dataclass
class LocalProject:
    """本地项目数据结构"""
    id: str
    name: str
    type: str  # file, code
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 文件信息
    file_path: Optional[str] = None
    original_name: Optional[str] = None
    file_size: int = 0
    file_hash: Optional[str] = None
    is_compressed: bool = False
    extracted_path: Optional[str] = None

    # 代码信息（代码类型项目）
    code_content: Optional[str] = None
    language: str = "python"

    # 执行配置
    entry_point: Optional[str] = None
    env_name: Optional[str] = None  # 关联的虚拟环境

    # 同步状态
    synced_from_master: bool = False
    master_project_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "file_path": self.file_path,
            "original_name": self.original_name,
            "file_size": self.file_size,
            "file_hash": self.file_hash,
            "is_compressed": self.is_compressed,
            "extracted_path": self.extracted_path,
            "code_content": self.code_content,
            "language": self.language,
            "entry_point": self.entry_point,
            "env_name": self.env_name,
            "synced_from_master": self.synced_from_master,
            "master_project_id": self.master_project_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LocalProject":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ProjectCacheStats:
    """项目缓存统计"""
    master_project_id: str
    local_project_id: str
    first_access: float      # 首次访问时间
    last_access: float       # 最后访问时间
    execution_count: int     # 执行次数
    avg_interval: float      # 平均执行间隔 (秒)

    def calculate_score(self, now: float) -> float:
        """计算缓存优先级分数 (越低越容易被淘汰)
        
        算法考虑因素:
        1. 执行频率: 执行次数越多，分数越高
        2. 最近活跃度: 最后访问时间越近，分数越高
        3. 执行周期: 平均间隔越短 (高频任务)，分数越高
        """
        # 距离最后访问的时间 (小时)
        hours_since_last = (now - self.last_access) / 3600

        # 基础分: 执行次数
        score = self.execution_count * 10

        # 活跃度加成: 最近访问的项目加分
        if hours_since_last < 1:
            score += 50  # 1小时内访问过
        elif hours_since_last < 6:
            score += 30  # 6小时内访问过
        elif hours_since_last < 24:
            score += 10  # 24小时内访问过

        # 高频任务加成: 平均间隔短的任务加分
        if self.avg_interval > 0:
            if self.avg_interval < 3600:  # 小于1小时
                score += 40
            elif self.avg_interval < 86400:  # 小于1天
                score += 20

        # 长期未使用惩罚
        if hours_since_last > 72:  # 超过3天未使用
            score -= 30
        if hours_since_last > 168:  # 超过7天未使用
            score -= 50

        return score


class LocalProjectService:
    """本地项目管理服务"""

    # 缓存配置
    CACHE_MAX_SIZE = 100       # 最大缓存项目数
    CACHE_MIN_KEEP = 20        # 最少保留项目数
    CACHE_TTL_HOURS = 168      # 缓存最大保留时间 (7天)
    CLEANUP_INTERVAL = 3600    # 清理检查间隔 (秒)

    def __init__(self, projects_dir: Optional[str] = None):
        self.projects_dir = projects_dir
        self._projects: Dict[str, LocalProject] = {}
        self._lock = asyncio.Lock()
        self._project_cache: Dict[str, str] = {}  # {master_project_id: local_project_id}
        self._cache_stats: Dict[str, ProjectCacheStats] = {}  # 缓存统计
        self._last_cleanup: float = 0

        # 新的 ProjectCache 实例（基于 file_hash 的缓存）
        # 在 set_projects_dir 中初始化
        self._file_cache: Optional[ProjectCache] = None

    def set_projects_dir(self, projects_dir: str):
        """设置项目目录"""
        self._projects_dir = projects_dir
        self.projects_dir = projects_dir
        os.makedirs(projects_dir, exist_ok=True)

        # 初始化 ProjectCache（基于 file_hash 的缓存）
        cache_dir = os.path.join(projects_dir, ".file_cache")
        self._file_cache = ProjectCache(
            cache_dir=cache_dir,
            max_size=self.CACHE_MAX_SIZE,
            ttl_hours=self.CACHE_TTL_HOURS,
        )

        # 同步加载已存在的项目
        self._load_projects_sync()

    @property
    def _index_file(self) -> str:
        """项目索引文件路径"""
        return os.path.join(self.projects_dir, ".projects_index.json")

    def _load_projects_sync(self):
        """同步加载项目索引"""
        loaded_from_index = False
        if os.path.exists(self._index_file):
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    data = ujson.load(f)
                self._projects = {
                    pid: LocalProject.from_dict(pdata)
                    for pid, pdata in data.items()
                }
                for pid, project in self._projects.items():
                    if project.master_project_id:
                        self._project_cache[project.master_project_id] = pid
                loaded_from_index = True
                logger.info(f"已加载 {len(self._projects)} 个项目, 缓存映射 {len(self._project_cache)} 条")
            except Exception as e:
                logger.error(f"加载项目索引失败: {e}")

        # 如果索引文件不存在或加载失败，扫描项目目录恢复
        if not loaded_from_index and self.projects_dir and os.path.exists(self.projects_dir):
            self._scan_and_recover_projects()

    async def _load_projects(self):
        """从磁盘加载项目索引"""
        async with self._lock:
            if not os.path.exists(self._index_file):
                return

            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    data = ujson.load(f)

                self._projects = {
                    pid: LocalProject.from_dict(pdata)
                    for pid, pdata in data.items()
                }

                # 恢复项目缓存映射（master_project_id -> local_project_id）
                for pid, project in self._projects.items():
                    if project.master_project_id:
                        self._project_cache[project.master_project_id] = pid

                logger.info(f"已加载 {len(self._projects)} 个项目, 缓存映射 {len(self._project_cache)} 条")
            except Exception as e:
                logger.error(f"加载项目索引失败: {e}")

    def _scan_and_recover_projects(self):
        """扫描项目目录，恢复丢失的项目索引"""
        if not self.projects_dir or not os.path.exists(self.projects_dir):
            return

        recovered = 0
        for item in os.listdir(self.projects_dir):
            item_path = os.path.join(self.projects_dir, item)
            if not os.path.isdir(item_path) or item.startswith('.'):
                continue

            project_id = item
            if project_id in self._projects:
                continue

            # 尝试恢复项目信息
            try:
                # 查找入口文件
                entry_point = None
                original_name = None
                for f in os.listdir(item_path):
                    if f.endswith('.py'):
                        entry_point = f
                        original_name = f
                        break

                if not entry_point:
                    # 检查是否有 extracted 目录
                    extracted_path = os.path.join(item_path, "extracted")
                    if os.path.exists(extracted_path):
                        for f in os.listdir(extracted_path):
                            if f.endswith('.py'):
                                entry_point = f
                                break

                project = LocalProject(
                    id=project_id,
                    name=f"recovered-{project_id}",
                    type="file",
                    entry_point=entry_point,
                    original_name=original_name,
                    file_path=item_path,
                )
                self._projects[project_id] = project
                recovered += 1
                logger.info(f"恢复项目: {project_id}, 入口: {entry_point}")
            except Exception as e:
                logger.warning(f"恢复项目 {project_id} 失败: {e}")

        if recovered > 0:
            # 同步保存索引
            try:
                data = {pid: p.to_dict() for pid, p in self._projects.items()}
                with open(self._index_file, "w", encoding="utf-8") as f:
                    ujson.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"已恢复 {recovered} 个项目并保存索引")
            except Exception as e:
                logger.error(f"保存恢复的项目索引失败: {e}")

    async def _save_projects(self):
        """保存项目索引到磁盘"""
        try:
            data = {pid: p.to_dict() for pid, p in self._projects.items()}
            with open(self._index_file, "w", encoding="utf-8") as f:
                ujson.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存项目索引失败: {e}")

    def _get_project_dir(self, project_id: str) -> str:
        """获取项目目录路径"""
        return os.path.join(self.projects_dir, project_id)

    def _calculate_file_hash(self, file_path: str) -> str:
        """计算文件哈希（使用统一的 hash_utils）"""
        return calculate_file_hash(file_path, "sha256")

    async def list_projects(self) -> List[Dict[str, Any]]:
        """列出所有项目"""
        return [p.to_dict() for p in self._projects.values()]

    async def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """获取项目详情"""
        project = self._projects.get(project_id)
        return project.to_dict() if project else None

    async def create_file_project(
        self,
        name: str,
        file_content: bytes,
        original_name: str,
        description: str = "",
        entry_point: Optional[str] = None,
        env_name: Optional[str] = None,
        master_project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建文件类型项目
        
        Args:
            name: 项目名称
            file_content: 文件内容（bytes）
            original_name: 原始文件名
            description: 项目描述
            entry_point: 入口文件
            env_name: 关联的虚拟环境名称
            master_project_id: 主节点项目ID
        """
        async with self._lock:
            project_id = str(uuid.uuid4())[:8]
            project_dir = self._get_project_dir(project_id)
            os.makedirs(project_dir, exist_ok=True)

            # 保存文件
            file_path = os.path.join(project_dir, original_name)
            with open(file_path, "wb") as f:
                f.write(file_content)

            file_hash = self._calculate_file_hash(file_path)
            file_size = len(file_content)

            # 检查是否是压缩文件并解压
            is_compressed = original_name.endswith(('.zip', '.tar.gz', '.tgz'))
            extracted_path = None

            if is_compressed:
                extracted_path = os.path.join(project_dir, "extracted")
                os.makedirs(extracted_path, exist_ok=True)

                try:
                    if original_name.endswith('.zip'):
                        with zipfile.ZipFile(file_path, 'r') as zf:
                            # 安全检查：防止路径遍历攻击
                            for member in zf.namelist():
                                member_path = os.path.normpath(member)
                                if member_path.startswith('..') or os.path.isabs(member_path):
                                    raise ValueError(f"检测到路径遍历攻击: {member}")
                            zf.extractall(extracted_path)
                    elif original_name.endswith(('.tar.gz', '.tgz')):
                        with tarfile.open(file_path, 'r:gz') as tf:
                            # 安全检查：使用 filter='data' 防止路径遍历（Python 3.12+）
                            # 对于旧版本，手动验证成员路径
                            safe_members = []
                            for member in tf.getmembers():
                                member_path = os.path.normpath(member.name)
                                if member_path.startswith('..') or os.path.isabs(member_path):
                                    logger.warning(f"跳过危险路径: {member.name}")
                                    continue
                                safe_members.append(member)
                            tf.extractall(extracted_path, members=safe_members)
                    logger.info(f"解压文件成功: {original_name} -> {extracted_path}")
                except Exception as e:
                    logger.error(f"解压文件失败: {e}")
                    extracted_path = None

            # 创建项目对象
            project = LocalProject(
                id=project_id,
                name=name,
                type="file",
                description=description,
                file_path=file_path,
                original_name=original_name,
                file_size=file_size,
                file_hash=file_hash,
                is_compressed=is_compressed,
                extracted_path=extracted_path,
                entry_point=entry_point,
                env_name=env_name,
                synced_from_master=master_project_id is not None,
                master_project_id=master_project_id,
            )

            self._projects[project_id] = project
            await self._save_projects()

            logger.info(f"创建文件项目成功: {name} ({project_id})")
            return project.to_dict()

    async def create_code_project(
        self,
        name: str,
        code_content: str,
        language: str = "python",
        description: str = "",
        entry_point: Optional[str] = None,
        env_name: Optional[str] = None,
        master_project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建代码类型项目
        
        Args:
            name: 项目名称
            code_content: 代码内容
            language: 编程语言
            description: 项目描述
            entry_point: 入口文件名
            env_name: 关联的虚拟环境名称
            master_project_id: 主节点项目ID
        """
        async with self._lock:
            project_id = str(uuid.uuid4())[:8]
            project_dir = self._get_project_dir(project_id)
            os.makedirs(project_dir, exist_ok=True)

            # 确定文件名
            if entry_point:
                filename = entry_point
            else:
                ext = {"python": ".py", "javascript": ".js", "typescript": ".ts"}.get(language, ".py")
                filename = f"main{ext}"

            # 保存代码文件
            file_path = os.path.join(project_dir, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code_content)

            # 创建项目对象
            project = LocalProject(
                id=project_id,
                name=name,
                type="code",
                description=description,
                file_path=file_path,
                original_name=filename,
                file_size=len(code_content.encode()),
                code_content=code_content,
                language=language,
                entry_point=entry_point or filename,
                env_name=env_name,
                synced_from_master=master_project_id is not None,
                master_project_id=master_project_id,
            )

            self._projects[project_id] = project
            await self._save_projects()

            logger.info(f"创建代码项目成功: {name} ({project_id})")
            return project.to_dict()

    async def update_project(
        self,
        project_id: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """更新项目"""
        async with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return None

            # 更新允许的字段
            allowed_fields = {
                "name", "description", "entry_point", "env_name",
                "code_content", "language"
            }

            for key, value in kwargs.items():
                if key in allowed_fields and value is not None:
                    setattr(project, key, value)

            # 如果更新了代码内容，同时更新文件
            if "code_content" in kwargs and project.type == "code":
                with open(project.file_path, "w", encoding="utf-8") as f:
                    f.write(kwargs["code_content"])
                project.file_size = len(kwargs["code_content"].encode())

            project.updated_at = datetime.now().isoformat()
            await self._save_projects()

            logger.info(f"更新项目成功: {project.name} ({project_id})")
            return project.to_dict()

    async def delete_project(self, project_id: str) -> bool:
        """删除项目"""
        async with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return False

            # 删除项目目录
            project_dir = self._get_project_dir(project_id)
            if os.path.exists(project_dir):
                shutil.rmtree(project_dir)

            del self._projects[project_id]
            await self._save_projects()

            logger.info(f"删除项目成功: {project.name} ({project_id})")
            return True

    async def get_project_files(self, project_id: str) -> List[Dict[str, Any]]:
        """
        获取项目文件列表
        """
        project = self._projects.get(project_id)
        if not project:
            return []

        files = []

        # 确定要扫描的目录
        if project.extracted_path and os.path.exists(project.extracted_path):
            scan_dir = project.extracted_path
        else:
            scan_dir = self._get_project_dir(project_id)

        for root, dirs, filenames in os.walk(scan_dir):
            # 忽略隐藏目录和常见的排除目录
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'__pycache__', 'node_modules', '.git'}]

            for filename in filenames:
                if filename.startswith('.'):
                    continue

                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, scan_dir)
                stat = os.stat(file_path)

                files.append({
                    "name": filename,
                    "path": rel_path,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "is_entry": rel_path == project.entry_point,
                })

        return files

    async def read_project_file(
        self,
        project_id: str,
        file_path: str
    ) -> Optional[str]:
        """读取项目文件内容"""
        project = self._projects.get(project_id)
        if not project:
            return None

        # 确定基础目录
        if project.extracted_path and os.path.exists(project.extracted_path):
            base_dir = project.extracted_path
        else:
            base_dir = self._get_project_dir(project_id)

        full_path = os.path.join(base_dir, file_path)

        # 安全检查：确保路径在项目目录内
        if not os.path.abspath(full_path).startswith(os.path.abspath(base_dir)):
            raise RuntimeError("非法文件路径")

        if not os.path.exists(full_path):
            return None

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            # 二进制文件
            return None

    async def write_project_file(
        self,
        project_id: str,
        file_path: str,
        content: str
    ) -> bool:
        """写入项目文件"""
        project = self._projects.get(project_id)
        if not project:
            return False

        # 确定基础目录
        if project.extracted_path and os.path.exists(project.extracted_path):
            base_dir = project.extracted_path
        else:
            base_dir = self._get_project_dir(project_id)

        full_path = os.path.join(base_dir, file_path)

        # 安全检查
        if not os.path.abspath(full_path).startswith(os.path.abspath(base_dir)):
            raise RuntimeError("非法文件路径")

        # 确保目录存在
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        # 更新项目时间戳
        project.updated_at = datetime.now().isoformat()
        await self._save_projects()

        return True

    async def bind_env(self, project_id: str, env_name: str) -> bool:
        """绑定虚拟环境到项目"""
        return await self.update_project(project_id, env_name=env_name) is not None

    async def unbind_env(self, project_id: str) -> bool:
        """解绑项目的虚拟环境"""
        return await self.update_project(project_id, env_name=None) is not None

    def get_project_entry_path(self, project_id: str) -> Optional[str]:
        """获取项目入口文件的完整路径"""
        project = self._projects.get(project_id)
        if not project:
            return None

        # 确定基础目录
        if project.extracted_path and os.path.exists(project.extracted_path):
            base_dir = project.extracted_path
        else:
            base_dir = self._get_project_dir(project_id)

        # 确定入口文件
        entry = project.entry_point
        if not entry:
            # 尝试找 main.py
            if os.path.exists(os.path.join(base_dir, "main.py")):
                entry = "main.py"
            else:
                # 找第一个 .py 文件
                for f in os.listdir(base_dir):
                    if f.endswith(".py"):
                        entry = f
                        break

        if not entry:
            return None

        return os.path.join(base_dir, entry)

    def get_project_work_dir(self, project_id: str) -> Optional[str]:
        """获取项目工作目录"""
        project = self._projects.get(project_id)
        if not project:
            return None

        if project.extracted_path and os.path.exists(project.extracted_path):
            return project.extracted_path

        return self._get_project_dir(project_id)

    async def sync_from_master(
        self,
        master_project_id: str,
        project_name: str,
        download_url: str,
        api_key: str,
        description: str = "",
        entry_point: Optional[str] = None,
        transfer_method: str = "original",
        file_hash: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        从主节点同步项目（智能拉取）
        
        优化策略：
        1. 缓存检查 - 基于 file_hash 验证（使用 ProjectCache）
        2. 网络 IO 不持锁 - 仅写入时加锁
        3. 完整性校验 - hash验证
        """
        # 使用 ProjectCache 检查缓存（基于 file_hash）
        if self._file_cache and file_hash:
            cached_path = self._file_cache.get(master_project_id, file_hash)
            if cached_path:
                # 从缓存路径找到对应的项目
                for pid, proj in self._projects.items():
                    if proj.file_path == cached_path or proj.extracted_path == cached_path:
                        logger.info(f"使用 ProjectCache 缓存: {project_name} (hash={file_hash[:8]}...)")
                        return proj.to_dict()

        # 回退到旧的缓存检查（兼容性）
        if master_project_id in self._project_cache:
            local_id = self._project_cache[master_project_id]
            cached_project = self._projects.get(local_id)

            if cached_project and file_hash and cached_project.file_hash == file_hash:
                logger.info(f"使用缓存: {project_name}")
                return cached_project.to_dict()

        logger.info(f"拉取项目: {project_name} [{transfer_method}]")

        # 网络 IO 不持锁
        import httpx
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {api_key}"}
            )

            if response.status_code != 200:
                raise RuntimeError(f"下载失败: {response.status_code}")

            file_content = response.content
            actual_size = len(file_content)

            # 从响应头获取实际的文件信息
            header_hash = response.headers.get("X-File-Hash")
            header_size = response.headers.get("X-File-Size")
            content_disposition = response.headers.get("Content-Disposition", "project.zip")

            # 验证文件大小
            expected_size = int(header_size) if header_size else file_size
            if expected_size and actual_size != expected_size:
                diff_ratio = abs(actual_size - expected_size) / expected_size
                if diff_ratio > 0.05:
                    logger.warning(f"大小差异过大: 预期{expected_size}, 实际{actual_size}")
                else:
                    logger.debug(f"大小略有差异: 预期{expected_size}, 实际{actual_size}")

            # Hash 验证
            expected_hash = header_hash or file_hash
            if expected_hash:
                md5_hash = calculate_content_hash(file_content, "md5")
                sha256_hash = calculate_content_hash(file_content, "sha256")

                if expected_hash in (md5_hash, sha256_hash):
                    logger.info(f"Hash验证通过: {expected_hash[:16]}...")
                else:
                    logger.warning(f"Hash不匹配: 预期{expected_hash[:16]}..., MD5={md5_hash[:16]}...")

        # 提取文件名
        original_name = content_disposition
        if "filename=" in original_name:
            original_name = original_name.split("filename=")[-1].strip('"')

        # 文件写入和索引更新加锁
        async with self._lock:
            project_id = str(uuid.uuid4())[:8]
            project_dir = self._get_project_dir(project_id)
            os.makedirs(project_dir, exist_ok=True)

            # 保存文件
            file_path = os.path.join(project_dir, original_name)
            with open(file_path, "wb") as f:
                f.write(file_content)

            file_hash_calc = self._calculate_file_hash(file_path)
            file_size_calc = len(file_content)

            # 检查是否是压缩文件并解压
            is_compressed = original_name.endswith(('.zip', '.tar.gz', '.tgz'))
            extracted_path = None

            if is_compressed:
                extracted_path = os.path.join(project_dir, "extracted")
                os.makedirs(extracted_path, exist_ok=True)

                try:
                    if original_name.endswith('.zip'):
                        with zipfile.ZipFile(file_path, 'r') as zf:
                            # 安全检查：防止路径遍历攻击
                            for member in zf.namelist():
                                member_path = os.path.normpath(member)
                                if member_path.startswith('..') or os.path.isabs(member_path):
                                    raise ValueError(f"检测到路径遍历攻击: {member}")
                            zf.extractall(extracted_path)
                    elif original_name.endswith(('.tar.gz', '.tgz')):
                        with tarfile.open(file_path, 'r:gz') as tf:
                            # 安全检查：使用 filter='data' 防止路径遍历（Python 3.12+）
                            # 对于旧版本，手动验证成员路径
                            safe_members = []
                            for member in tf.getmembers():
                                member_path = os.path.normpath(member.name)
                                if member_path.startswith('..') or os.path.isabs(member_path):
                                    logger.warning(f"跳过危险路径: {member.name}")
                                    continue
                                safe_members.append(member)
                            tf.extractall(extracted_path, members=safe_members)
                    logger.info(f"解压文件成功: {original_name} -> {extracted_path}")
                except Exception as e:
                    logger.error(f"解压文件失败: {e}")
                    extracted_path = None

            # 创建项目对象
            project = LocalProject(
                id=project_id,
                name=project_name,
                type="file",
                description=description,
                file_path=file_path,
                original_name=original_name,
                file_size=file_size_calc,
                file_hash=file_hash_calc,
                is_compressed=is_compressed,
                extracted_path=extracted_path,
                entry_point=entry_point,
                synced_from_master=True,
                master_project_id=master_project_id,
            )

            self._projects[project_id] = project
            await self._save_projects()

            # 缓存并初始化统计
            import time
            now = time.time()
            self._project_cache[master_project_id] = project_id
            self._cache_stats[master_project_id] = ProjectCacheStats(
                master_project_id=master_project_id,
                local_project_id=project_id,
                first_access=now,
                last_access=now,
                execution_count=0,
                avg_interval=0
            )

            # 添加到 ProjectCache（基于 file_hash 的缓存）
            if self._file_cache and file_hash_calc:
                # 使用项目目录或解压目录作为缓存路径
                cache_path = extracted_path if extracted_path else project_dir
                await self._file_cache.put(master_project_id, file_hash_calc, cache_path)

            logger.info(f"同步完成: {project_name} ({file_size_calc} bytes)")

            return project.to_dict()

    def get_cached_project_id(self, master_project_id: str) -> Optional[str]:
        """获取缓存项目ID (带智能淘汰)"""
        import time

        local_id = self._project_cache.get(master_project_id)
        if local_id:
            now = time.time()
            # 更新缓存统计
            if master_project_id in self._cache_stats:
                stats = self._cache_stats[master_project_id]
                # 计算新的平均间隔
                if stats.execution_count > 0:
                    interval = now - stats.last_access
                    stats.avg_interval = (
                        (stats.avg_interval * stats.execution_count + interval) 
                        / (stats.execution_count + 1)
                    )
                stats.last_access = now
                stats.execution_count += 1
            else:
                self._cache_stats[master_project_id] = ProjectCacheStats(
                    master_project_id=master_project_id,
                    local_project_id=local_id,
                    first_access=now,
                    last_access=now,
                    execution_count=1,
                    avg_interval=0
                )

            # 定期检查是否需要清理缓存
            if now - self._last_cleanup > self.CLEANUP_INTERVAL:
                self._smart_evict_cache()
                self._last_cleanup = now

        return local_id

    def _smart_evict_cache(self):
        """智能淘汰缓存 - 基于执行频率和活跃度"""
        import time

        cache_count = len(self._project_cache)
        if cache_count <= self.CACHE_MIN_KEEP:
            return

        now = time.time()
        ttl_seconds = self.CACHE_TTL_HOURS * 3600

        # 计算每个缓存项的分数
        scored_items = []
        for master_id, local_id in self._project_cache.items():
            stats = self._cache_stats.get(master_id)
            if stats:
                score = stats.calculate_score(now)
                age = now - stats.first_access
            else:
                # 没有统计信息的项目，给予较低分数
                score = 0
                age = ttl_seconds + 1

            scored_items.append((master_id, score, age))

        # 按分数排序 (分数低的优先淘汰)
        scored_items.sort(key=lambda x: x[1])

        evicted = 0
        for master_id, score, age in scored_items:
            # 保留最少数量
            if cache_count - evicted <= self.CACHE_MIN_KEEP:
                break

            # 淘汰条件:
            # 1. 超过 TTL
            # 2. 分数低于阈值且缓存数量超过限制
            should_evict = (
                age > ttl_seconds or 
                (score < 0 and cache_count - evicted > self.CACHE_MAX_SIZE * 0.8)
            )

            if should_evict:
                self._evict_project(master_id)
                evicted += 1
                logger.info(f"淘汰项目缓存: {master_id} (分数: {score:.1f})")

        if evicted > 0:
            logger.info(f"缓存清理完成: 淘汰 {evicted} 个, 剩余 {cache_count - evicted} 个")

    def _evict_project(self, master_project_id: str):
        """淘汰单个项目缓存"""
        local_id = self._project_cache.pop(master_project_id, None)
        self._cache_stats.pop(master_project_id, None)

        # 可选: 删除本地项目文件 (节省磁盘空间)
        # if local_id and local_id in self._projects:
        #     project_dir = self._get_project_dir(local_id)
        #     if os.path.exists(project_dir):
        #         shutil.rmtree(project_dir, ignore_errors=True)
        #     del self._projects[local_id]

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        import time
        now = time.time()

        stats_list = []
        for master_id, stats in self._cache_stats.items():
            stats_list.append({
                "master_project_id": master_id,
                "execution_count": stats.execution_count,
                "avg_interval_hours": round(stats.avg_interval / 3600, 2),
                "hours_since_last": round((now - stats.last_access) / 3600, 2),
                "score": round(stats.calculate_score(now), 1)
            })

        # 按分数排序
        stats_list.sort(key=lambda x: x["score"], reverse=True)

        result = {
            "total_cached": len(self._project_cache),
            "max_size": self.CACHE_MAX_SIZE,
            "projects": stats_list[:20]  # 只返回前20个
        }

        # 添加 ProjectCache 统计信息
        if self._file_cache:
            result["file_cache"] = self._file_cache.get_stats()

        return result

    def clear_cache(self, master_project_id: Optional[str] = None):
        """清理缓存"""
        if master_project_id:
            self._project_cache.pop(master_project_id, None)
        else:
            self._project_cache.clear()
            # 同时清理 ProjectCache
            if self._file_cache:
                self._file_cache.clear()

    async def load_cache_index(self) -> None:
        """
        从磁盘加载缓存索引（Worker 启动时调用）
        
        Requirements: 2.6
        """
        if self._file_cache:
            await self._file_cache.load_index()
            logger.info("ProjectCache 索引已加载")

    async def save_cache_index(self) -> None:
        """
        保存缓存索引到磁盘（Worker 停止时调用）
        
        Requirements: 2.7
        """
        if self._file_cache:
            await self._file_cache.save_index()
            logger.info("ProjectCache 索引已保存")


# 全局实例
local_project_service = LocalProjectService()

