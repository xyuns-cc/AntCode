"""
运行时构建器

实现 uv venv / uv sync 构建，确保 reproducible behavior。

Requirements: 6.4
"""

import asyncio
import contextlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import ujson
from loguru import logger

from antcode_worker.runtime.hash import compute_runtime_hash
from antcode_worker.runtime.spec import RuntimeSpec
from antcode_worker.runtime.uv_manager import CommandResult, run_command


@dataclass
class BuildResult:
    """构建结果"""

    success: bool
    venv_path: str
    runtime_hash: str
    python_executable: str
    python_version: str | None = None
    error_message: str | None = None
    build_time_ms: float = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "venv_path": self.venv_path,
            "runtime_hash": self.runtime_hash,
            "python_executable": self.python_executable,
            "python_version": self.python_version,
            "error_message": self.error_message,
            "build_time_ms": self.build_time_ms,
            "cached": self.cached,
        }


class RuntimeBuilder:
    """
    运行时构建器

    使用 uv 工具构建 Python 虚拟环境，确保可重现性。

    Requirements: 6.4
    """

    def __init__(
        self,
        venvs_dir: str,
        timeout: int = 600,
        uv_cache_dir: str | None = None,
    ):
        """
        初始化构建器

        Args:
            venvs_dir: 虚拟环境存储目录
            timeout: 构建超时时间（秒）
            uv_cache_dir: uv 缓存目录
        """
        self.venvs_dir = venvs_dir
        self.timeout = timeout
        self.uv_cache_dir = uv_cache_dir

        # 确保目录存在
        os.makedirs(venvs_dir, exist_ok=True)

    def _get_venv_path(self, runtime_hash: str) -> str:
        """获取虚拟环境路径"""
        return os.path.join(self.venvs_dir, runtime_hash)

    def _get_python_executable(self, venv_path: str) -> str:
        """获取虚拟环境中的 Python 可执行文件路径"""
        if os.name == "nt":
            return os.path.join(venv_path, "Scripts", "python.exe")
        return os.path.join(venv_path, "bin", "python")

    def _get_manifest_path(self, venv_path: str) -> str:
        """获取清单文件路径"""
        return os.path.join(venv_path, "manifest.json")

    async def _get_python_version(self, python_exe: str) -> str | None:
        """获取 Python 版本"""
        result = await run_command([python_exe, "--version"], timeout=10)
        if result.exit_code == 0:
            # "Python 3.11.5" -> "3.11.5"
            return result.stdout.strip().split()[-1]
        return None

    async def _create_venv(
        self,
        venv_path: str,
        spec: RuntimeSpec,
    ) -> CommandResult:
        """
        创建虚拟环境

        Args:
            venv_path: 虚拟环境路径
            spec: 运行时规格

        Returns:
            命令执行结果
        """
        args = ["uv", "venv", venv_path]

        # 添加 Python 版本参数
        if spec.python_spec.path:
            args.extend(["--python", spec.python_spec.path])
        elif spec.python_spec.version:
            args.extend(["--python", f"python@{spec.python_spec.version}"])
        else:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr="runtime python_spec.path 或 python_spec.version 不能为空",
            )

        # 设置环境变量
        env = {}
        if self.uv_cache_dir:
            env["UV_CACHE_DIR"] = self.uv_cache_dir

        result = await run_command(args, env=env if env else None, timeout=self.timeout)

        return result

    async def _install_requirements(
        self,
        venv_path: str,
        spec: RuntimeSpec,
    ) -> CommandResult:
        """
        安装依赖

        Args:
            venv_path: 虚拟环境路径
            spec: 运行时规格

        Returns:
            命令执行结果
        """
        python_exe = self._get_python_executable(venv_path)
        requirements = spec.lock_source.requirements

        if not requirements:
            return CommandResult(exit_code=0, stdout="", stderr="")

        temp_dir = os.path.join(os.path.dirname(self.venvs_dir), "temp", "runtime-build")
        os.makedirs(temp_dir, exist_ok=True)

        # 创建临时 requirements 文件
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
            dir=temp_dir,
        ) as f:
            f.write("\n".join(requirements))
            requirements_file = f.name

        try:
            # 使用 uv pip install
            args = [
                "uv",
                "pip",
                "install",
                "--python",
                python_exe,
                "-r",
                requirements_file,
            ]

            # 添加约束
            if spec.constraints:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".txt",
                    delete=False,
                    encoding="utf-8",
                    dir=temp_dir,
                ) as cf:
                    cf.write("\n".join(spec.constraints))
                    constraints_file = cf.name
                args.extend(["-c", constraints_file])
            else:
                constraints_file = None

            # 设置环境变量
            env = {}
            if self.uv_cache_dir:
                env["UV_CACHE_DIR"] = self.uv_cache_dir

            result = await run_command(
                args,
                env=env if env else None,
                timeout=self.timeout,
            )

            # 清理约束文件
            if constraints_file:
                with contextlib.suppress(OSError):
                    os.unlink(constraints_file)

            return result

        finally:
            # 清理临时文件
            with contextlib.suppress(OSError):
                os.unlink(requirements_file)

    def _save_manifest(
        self,
        venv_path: str,
        spec: RuntimeSpec,
        runtime_hash: str,
        python_version: str | None,
    ) -> None:
        """保存清单文件"""
        manifest = {
            "runtime_hash": runtime_hash,
            "spec": spec.to_dict(),
            "python_version": python_version,
            "created_at": datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
        }

        manifest_path = self._get_manifest_path(venv_path)
        with open(manifest_path, "w", encoding="utf-8") as f:
            ujson.dump(manifest, f, ensure_ascii=False, indent=2)

    def _load_manifest(self, venv_path: str) -> dict[str, Any] | None:
        """加载清单文件"""
        manifest_path = self._get_manifest_path(venv_path)
        if not os.path.exists(manifest_path):
            return None

        try:
            with open(manifest_path, encoding="utf-8") as f:
                return ujson.load(f)
        except Exception:
            return None

    def _update_last_used(self, venv_path: str) -> None:
        """更新最后使用时间"""
        manifest = self._load_manifest(venv_path)
        if manifest:
            manifest["last_used"] = datetime.now().isoformat()
            manifest_path = self._get_manifest_path(venv_path)
            with open(manifest_path, "w", encoding="utf-8") as f:
                ujson.dump(manifest, f, ensure_ascii=False, indent=2)

    def exists(self, runtime_hash: str) -> bool:
        """检查运行时是否已存在"""
        venv_path = self._get_venv_path(runtime_hash)
        python_exe = self._get_python_executable(venv_path)
        return os.path.exists(python_exe)

    async def build(
        self,
        spec: RuntimeSpec,
        force_rebuild: bool = False,
    ) -> BuildResult:
        """
        构建运行时环境

        Args:
            spec: 运行时规格
            force_rebuild: 是否强制重建

        Returns:
            构建结果

        Requirements: 6.4
        """
        start_time = asyncio.get_event_loop().time()

        # 计算哈希
        runtime_hash = compute_runtime_hash(spec)
        venv_path = self._get_venv_path(runtime_hash)
        python_exe = self._get_python_executable(venv_path)

        # 检查是否已存在
        if not force_rebuild and os.path.exists(python_exe):
            logger.info(f"运行时已存在，复用缓存: {runtime_hash}")
            self._update_last_used(venv_path)

            python_version = await self._get_python_version(python_exe)

            return BuildResult(
                success=True,
                venv_path=venv_path,
                runtime_hash=runtime_hash,
                python_executable=python_exe,
                python_version=python_version,
                cached=True,
                build_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
            )

        logger.info(f"开始构建运行时: {runtime_hash}")

        # 清理旧目录（如果存在）
        if os.path.exists(venv_path):
            import shutil

            shutil.rmtree(venv_path)

        try:
            # 创建虚拟环境
            result = await self._create_venv(venv_path, spec)
            if result.exit_code != 0:
                return BuildResult(
                    success=False,
                    venv_path=venv_path,
                    runtime_hash=runtime_hash,
                    python_executable=python_exe,
                    error_message=f"创建虚拟环境失败: {result.stderr}",
                    build_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
                )

            # 安装依赖
            result = await self._install_requirements(venv_path, spec)

            if result.exit_code != 0:
                return BuildResult(
                    success=False,
                    venv_path=venv_path,
                    runtime_hash=runtime_hash,
                    python_executable=python_exe,
                    error_message=f"安装依赖失败: {result.stderr}",
                    build_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
                )

            # 获取 Python 版本
            python_version = await self._get_python_version(python_exe)

            # 保存清单
            self._save_manifest(venv_path, spec, runtime_hash, python_version)

            build_time = (asyncio.get_event_loop().time() - start_time) * 1000
            logger.info(f"运行时构建完成: {runtime_hash}, 耗时: {build_time:.0f}ms")

            return BuildResult(
                success=True,
                venv_path=venv_path,
                runtime_hash=runtime_hash,
                python_executable=python_exe,
                python_version=python_version,
                build_time_ms=build_time,
            )

        except Exception as e:
            logger.error(f"构建运行时异常: {e}")
            return BuildResult(
                success=False,
                venv_path=venv_path,
                runtime_hash=runtime_hash,
                python_executable=python_exe,
                error_message=str(e),
                build_time_ms=(asyncio.get_event_loop().time() - start_time) * 1000,
            )

    async def remove(self, runtime_hash: str) -> bool:
        """
        删除运行时环境

        Args:
            runtime_hash: 运行时哈希

        Returns:
            是否成功删除
        """
        venv_path = self._get_venv_path(runtime_hash)

        if not os.path.exists(venv_path):
            return False

        try:
            import shutil

            shutil.rmtree(venv_path)
            logger.info(f"已删除运行时: {runtime_hash}")
            return True
        except Exception as e:
            logger.error(f"删除运行时失败: {e}")
            return False

    async def list_runtimes(self) -> list[dict[str, Any]]:
        """
        列出所有运行时

        Returns:
            运行时信息列表
        """
        runtimes: list[dict[str, Any]] = []

        if not os.path.exists(self.venvs_dir):
            return runtimes

        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if not os.path.isdir(venv_path):
                continue

            python_exe = self._get_python_executable(venv_path)
            if not os.path.exists(python_exe):
                continue

            manifest = self._load_manifest(venv_path)

            runtimes.append(
                {
                    "runtime_hash": name,
                    "venv_path": venv_path,
                    "python_executable": python_exe,
                    "python_version": manifest.get("python_version") if manifest else None,
                    "created_at": manifest.get("created_at") if manifest else None,
                    "last_used": manifest.get("last_used") if manifest else None,
                }
            )

        return runtimes
