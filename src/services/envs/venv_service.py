from __future__ import annotations

import asyncio
import os

from loguru import logger

from src.core.command_runner import run_command
from src.core.config import settings
from src.services.envs.python_env_service import python_env_service
from src.models import Interpreter, Venv, ProjectVenvBinding
from src.utils.serialization import from_json, to_json, json_load_file, json_dump_file


_venv_locks = {}


def _venv_lock(project_id):
    if project_id not in _venv_locks:
        _venv_locks[project_id] = asyncio.Lock()
    return _venv_locks[project_id]


def _paths_for(project_id, version):
    venv_root = os.path.join(settings.VENV_STORAGE_ROOT, str(project_id))
    venv_dir = os.path.join(venv_root, version)
    manifest = os.path.join(venv_root, "manifest.json")
    return {"root": venv_root, "dir": venv_dir, "manifest": manifest}


class ProjectVenvService:
    """项目虚拟环境管理（基于 uv venv）。"""

    async def get_status(self, project_id):
        """Return project venv status."""
        root = os.path.join(os.path.abspath(os.path.join(os.getcwd(), "storage")), "venvs", str(project_id))
        manifest_path = os.path.join(root, "manifest.json")
        if os.path.exists(manifest_path):
            try:
                data = json_load_file(manifest_path)
                version = data.get("version")
                venv_dir = os.path.join(root, version) if version else None
                return {"project_id": str(project_id), "version": version, "venv_path": venv_dir}
            except Exception:
                pass
        return {"project_id": str(project_id), "version": None, "venv_path": None}

    async def create_or_use(
        self,
        project_id,
        version,
        create_if_missing=True,
        created_by=None,
        interpreter_source="mise",
        python_bin=None,
    ):
        """Create or reuse venv and bind to interpreter version."""
        lock = _venv_lock(str(project_id))
        async with lock:
            paths = _paths_for(str(project_id), version)
            os.makedirs(paths["root"], exist_ok=True)

            # 确保解释器存在/可用
            if interpreter_source == "local":
                if not python_bin:
                    raise RuntimeError("本地解释器需要提供 python_bin")
                interp = await python_env_service.register_local(python_bin, created_by=created_by)
                python_exe = interp["python_bin"]
            else:
                interp = await python_env_service.ensure_installed(version, created_by=created_by)
                python_exe = interp["python_bin"]

            # 已存在直接返回
            if os.path.exists(paths["dir"]):
                await self._write_manifest(paths["manifest"], version)
                return {"project_id": str(project_id), "version": version, "venv_path": paths["dir"]}

            if not create_if_missing:
                raise RuntimeError("虚拟环境不存在且不允许创建")

            # 创建 venv
            res = await run_command(["uv", "venv", paths["dir"], "--python", python_exe], timeout=900)
            if res.exit_code != 0:
                raise RuntimeError(f"创建虚拟环境失败: {res.stderr.strip()}")

            await self._write_manifest(paths["manifest"], version)

            # 记录/更新 Venv 与绑定
            interpreter = await Interpreter.filter(tool="python", version=version).first()
            if not interpreter:
                raise RuntimeError(f"未找到 Python {version} 解释器")
            venv_obj = await Venv.get_or_none(venv_path=paths["dir"])
            if not venv_obj:
                venv_obj = await Venv.create(
                    scope="private",
                    key=None,
                    version=version,
                    venv_path=paths["dir"],
                    interpreter=interpreter,
                    created_by=created_by,
                )
            # 更新项目绑定（当前）
            await ProjectVenvBinding.filter(project_id=int(project_id), is_current=True).update(is_current=False)
            await ProjectVenvBinding.create(project_id=int(project_id), venv=venv_obj, is_current=True, created_by=created_by)

            return {"project_id": str(project_id), "version": version, "venv_path": paths["dir"]}

    async def delete(self, project_id):
        """Delete project venv (does not affect interpreter)."""
        lock = _venv_lock(str(project_id))
        async with lock:
            root = os.path.join(os.path.abspath(os.path.join(os.getcwd(), "storage")), "venvs", str(project_id))
            if not os.path.exists(root):
                return True
            # 保守删除：仅移除 manifest 和绑定版本目录
            manifest = os.path.join(root, "manifest.json")
            version = None
            if os.path.exists(manifest):
                try:
                    data = json_load_file(manifest)
                    version = data.get("version")
                except Exception:
                    pass
                try:
                    os.remove(manifest)
                except FileNotFoundError:
                    pass
            # 删除当前绑定版本目录
            if version:
                venv_dir = os.path.join(root, version)
                await self._safe_rmtree(venv_dir)
            return True

    async def create_or_use_shared(
        self,
        version,
        key=None,
        created_by=None,
        interpreter_source="mise",
        python_bin=None,
    ):
        """Create or reuse shared venv (by version or custom key)."""
        ident = key or version
        venv_root = os.path.join(settings.VENV_STORAGE_ROOT, "shared")
        venv_dir = os.path.join(venv_root, ident)
        manifest = os.path.join(venv_dir, "manifest.json")

        os.makedirs(venv_root, exist_ok=True)
        # 确保解释器存在
        if interpreter_source == "local":
            if not python_bin:
                raise RuntimeError("本地解释器需要提供 python_bin")
            interp = await python_env_service.register_local(python_bin, created_by=created_by)
            python_exe = interp["python_bin"]
        else:
            interp = await python_env_service.ensure_installed(version, created_by=created_by)
            python_exe = interp["python_bin"]

        if not os.path.exists(venv_dir):
            os.makedirs(venv_dir, exist_ok=True)
            res = await run_command(["uv", "venv", venv_dir, "--python", python_exe], timeout=900)
            if res.exit_code != 0:
                raise RuntimeError(f"创建共享虚拟环境失败: {res.stderr.strip()}")

        # 写入manifest
        json_dump_file({"version": version, "key": ident}, manifest)

        # 记录/更新 Venv
        interpreter = await Interpreter.filter(tool="python", version=version).first()
        if not interpreter:
            raise RuntimeError(f"未找到 Python {version} 解释器")
        venv_obj = await Venv.get_or_none(venv_path=venv_dir)
        if not venv_obj:
            venv_obj = await Venv.create(
                scope="shared",
                key=ident,
                version=version,
                venv_path=venv_dir,
                interpreter=interpreter,
                created_by=created_by,
            )

        return {"version": version, "venv_path": venv_dir, "key": ident, "venv_id": venv_obj.public_id}

    def venv_python(self, venv_dir):
        candidates = [
            os.path.join(venv_dir, "bin", "python"),
            os.path.join(venv_dir, "bin", "python3"),
            os.path.join(venv_dir, "Scripts", "python.exe"),
        ]
        return next((p for p in candidates if os.path.exists(p)), candidates[0])

    async def install_dependencies(self, venv_dir, packages):
        if not packages:
            return
        py = self.venv_python(venv_dir)
        # 使用 uv pip 安装到指定 python
        args = ["uv", "pip", "install", "-q", "--python", py]
        args.extend(packages)
        res = await run_command(args, timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"安装依赖失败: {res.stderr.strip() or res.stdout.strip()}")

    async def list_packages(self, venv_dir):
        py = self.venv_python(venv_dir)
        # 优先 uv pip list，回退 pip list
        res = await run_command(["uv", "pip", "list", "--format", "json", "--python", py], timeout=120)
        if res.exit_code != 0 or not res.stdout.strip():
            res = await run_command([py, "-m", "pip", "list", "--format", "json"], timeout=120)
        if res.exit_code != 0:
            raise RuntimeError(f"获取依赖列表失败: {res.stderr.strip()}")
        try:
            return from_json(res.stdout)
        except Exception:
            return []

    async def delete_shared_by_id(self, venv_id):
        from src.models import ProjectVenvBinding, Project
        venv = await Venv.get(id=venv_id)
        # 仅允许删除共享环境，且无任何绑定记录
        if venv.scope != "shared":
            raise RuntimeError("仅共享环境可直接删除")
        bindings = await ProjectVenvBinding.filter(venv_id=venv_id).all()
        if bindings:
            # 获取使用该环境的项目名称
            project_ids = [b.project_id for b in bindings]
            projects = await Project.filter(id__in=project_ids).all()
            project_names = [p.name for p in projects]
            raise RuntimeError(f"该虚拟环境正在被以下项目使用，无法删除: {', '.join(project_names)}")
        await self._safe_rmtree(venv.venv_path)
        await venv.delete()
        return True

    async def _safe_rmtree(self, path):
        if not path or not os.path.exists(path):
            return
        # 避免误删，必须在 storage/venvs 下
        venvs_root = settings.VENV_STORAGE_ROOT
        abs_path = os.path.abspath(path)
        if not abs_path.startswith(os.path.abspath(venvs_root) + os.sep):
            logger.warning(f"拒绝删除非 venv 路径: {abs_path}")
            return
        # 递归删除
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

    async def _write_manifest(self, manifest_path, version):
        data = {"version": version}
        json_dump_file(data, manifest_path)


project_venv_service = ProjectVenvService()
