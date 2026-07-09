"""
UV 运行时管理器

负责虚拟环境的创建、删除、包管理等。
基于 uv 工具实现高效的 Python 环境管理。

支持的操作系统:
- Linux
- macOS
- Windows
"""

import asyncio
import os
import platform
import re
import shutil
from dataclasses import dataclass
from datetime import datetime

import ujson
from loguru import logger

# 操作系统检测
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@/+=:~\\-\\[\\]\\(\\),<>!#]*$")


@dataclass
class CommandResult:
    """命令执行结果"""

    exit_code: int
    stdout: str
    stderr: str


async def run_command(
    args: list[str],
    cwd: str | None = None,
    env: dict | None = None,
    timeout: int = 900,
) -> CommandResult:
    """执行命令。argv[0] 会经 shutil.which 解析（Windows 上匹配 .cmd/.exe/.bat）。"""
    from antcode_worker.runtime.win_exec import resolve_argv

    final_env = os.environ.copy()
    if env:
        final_env.update(env)

    # Windows-safe: 把 argv[0] 换成绝对路径（npm→npm.cmd 等）；Unix 也走此逻辑保证一致
    resolved_args = resolve_argv(args)
    cmd_str = " ".join(resolved_args)
    logger.debug(f"执行命令: {cmd_str}")

    try:
        process = await asyncio.create_subprocess_exec(
            *resolved_args,
            cwd=cwd,
            env=final_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)

        stdout = stdout_b.decode(errors="ignore") if stdout_b else ""
        stderr = stderr_b.decode(errors="ignore") if stderr_b else ""

        return CommandResult(exit_code=process.returncode or 0, stdout=stdout, stderr=stderr)
    except TimeoutError:
        return CommandResult(exit_code=124, stdout="", stderr=f"命令超时: {cmd_str}")
    except FileNotFoundError:
        return CommandResult(exit_code=127, stdout="", stderr=f"命令未找到: {args[0]}")
    except Exception as e:
        return CommandResult(exit_code=-1, stdout="", stderr=str(e))


class UVManager:
    """
    UV 运行时管理器

    使用 uv 工具管理 Python 虚拟环境。
    """

    def __init__(self, venvs_dir: str | None = None):
        self.venvs_dir = venvs_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._env_count_cache = 0

    def set_venvs_dir(self, venvs_dir: str) -> None:
        """设置虚拟环境目录"""
        self.venvs_dir = venvs_dir
        os.makedirs(venvs_dir, exist_ok=True)
        self._update_env_count_cache()

    def _update_env_count_cache(self) -> None:
        """更新环境数量缓存（同步方法）"""
        if not self.venvs_dir or not os.path.exists(self.venvs_dir):
            self._env_count_cache = 0
            return

        count = 0
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if os.path.isdir(venv_path):
                bin_dir = os.path.join(venv_path, "bin")
                scripts_dir = os.path.join(venv_path, "Scripts")
                if os.path.exists(bin_dir) or os.path.exists(scripts_dir):
                    count += 1
        self._env_count_cache = count

    def get_env_count(self) -> int:
        """获取环境数量（同步方法，用于指标收集）"""
        self._update_env_count_cache()
        return self._env_count_cache

    def _get_lock(self, key: str) -> asyncio.Lock:
        """获取指定 key 的锁"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _get_venv_path(self, env_name: str) -> str:
        """获取虚拟环境路径"""
        if not self.venvs_dir:
            raise RuntimeError("venvs_dir 未设置")
        return os.path.join(self.venvs_dir, env_name)

    def _get_python_executable(self, venv_path: str) -> str:
        """获取虚拟环境中的 Python 可执行文件路径"""
        if os.name == "nt":
            candidates = [os.path.join(venv_path, "Scripts", "python.exe")]
        else:
            candidates = [
                os.path.join(venv_path, "bin", "python"),
                os.path.join(venv_path, "bin", "python3"),
            ]
        return next((p for p in candidates if os.path.exists(p)), candidates[0])

    async def list_envs(self, scope: str | None = None) -> list[dict]:
        """列出所有虚拟环境"""
        if not self.venvs_dir or not os.path.exists(self.venvs_dir):
            return []

        envs = []
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if not os.path.isdir(venv_path):
                continue

            python_exe = self._get_python_executable(venv_path)
            if not os.path.exists(python_exe):
                continue

            manifest_path = os.path.join(venv_path, "manifest.json")
            manifest = {}
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest = ujson.load(f)
                except ValueError as exc:
                    raise RuntimeError(f"运行时清单文件无效: {manifest_path}") from exc

            version = manifest.get("python_version", "unknown")
            if version == "unknown":
                res = await run_command([python_exe, "--version"], timeout=10)
                if res.exit_code == 0:
                    version = res.stdout.strip().split()[-1]
                    manifest["python_version"] = version
                    manifest.setdefault("created_at", datetime.now().isoformat())
                    manifest.setdefault("name", name)
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        ujson.dump(manifest, f, ensure_ascii=False, indent=2)

            scope_value = "shared" if name.startswith("shared-") else "private"

            if scope and scope_value != scope:
                continue

            envs.append(
                {
                    "name": name,
                    "path": venv_path,
                    "python_version": version,
                    "python_executable": python_exe,
                    "created_at": manifest.get("created_at"),
                    "created_by": manifest.get("created_by"),
                    "packages_count": manifest.get("packages_count", 0),
                    "key": manifest.get("key"),
                    "description": manifest.get("description"),
                    "scope": scope_value,
                }
            )

        return envs

    async def get_env(self, env_name: str) -> dict | None:
        """获取虚拟环境详情"""
        envs = await self.list_envs()
        return next((e for e in envs if e["name"] == env_name), None)

    async def update_env(
        self,
        env_name: str,
        key: str | None = None,
        description: str | None = None,
    ) -> dict:
        """更新虚拟环境元数据（manifest）"""
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)
            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            manifest_path = os.path.join(venv_path, "manifest.json")
            manifest: dict = {}
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        manifest = ujson.load(f)
                except Exception:
                    manifest = {}

            manifest.setdefault("name", env_name)
            manifest.setdefault("created_at", datetime.now().isoformat())

            if key is None:
                manifest.pop("key", None)
            else:
                normalized_key = key.strip()
                if normalized_key:
                    manifest["key"] = normalized_key
                else:
                    manifest.pop("key", None)

            if description is None:
                manifest.pop("description", None)
            else:
                normalized_desc = description.strip()
                if normalized_desc:
                    manifest["description"] = normalized_desc
                else:
                    manifest.pop("description", None)

            with open(manifest_path, "w", encoding="utf-8") as f:
                ujson.dump(manifest, f, ensure_ascii=False, indent=2)

            env_data = await self.get_env(env_name)
            if not env_data:
                raise RuntimeError("环境更新后读取失败")

            return env_data

    async def create_env(
        self,
        env_name: str,
        python_version: str | None = None,
        packages: list[str] | None = None,
        created_by: str | None = None,
    ) -> dict:
        """
        创建虚拟环境

        Args:
            env_name: 环境名称
            python_version: Python版本（如 "3.12"），为空则使用当前Python
            packages: 要安装的包列表
            created_by: 创建人用户名
        """
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 已存在")

            if not python_version:
                raise RuntimeError("python_version 不能为空")

            python_arg = f"python@{python_version}"
            res = await run_command(["uv", "venv", venv_path, "--python", python_arg], timeout=600)
            if res.exit_code != 0:
                raise RuntimeError(f"创建虚拟环境失败: {res.stderr or res.stdout}")

            python_exe = self._get_python_executable(venv_path)
            version_res = await run_command([python_exe, "--version"], timeout=10)
            actual_version = version_res.stdout.strip().split()[-1] if version_res.exit_code == 0 else "unknown"

            manifest = {
                "name": env_name,
                "python_version": actual_version,
                "created_at": datetime.now().isoformat(),
                "created_by": created_by,
                "packages_count": 0,
            }
            manifest_path = os.path.join(venv_path, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                ujson.dump(manifest, f, ensure_ascii=False, indent=2)

            if packages:
                await self.install_packages(env_name, packages)

            await self._update_packages_count(env_name)

            with open(manifest_path, encoding="utf-8") as f:
                manifest = ujson.load(f)

            logger.info(f"虚拟环境创建成功: {env_name} (Python {actual_version})")

            return {
                "name": env_name,
                "path": venv_path,
                "python_version": actual_version,
                "python_executable": python_exe,
                "created_at": manifest["created_at"],
                "created_by": manifest.get("created_by"),
                "packages_count": manifest.get("packages_count", 0),
            }

    async def delete_env(self, env_name: str) -> bool:
        """删除虚拟环境"""
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                return False

            try:
                shutil.rmtree(venv_path)
                logger.info(f"虚拟环境删除成功: {env_name}")
                return True
            except Exception as e:
                logger.error(f"删除虚拟环境失败: {e}")
                raise RuntimeError(f"删除虚拟环境失败: {e}")

    async def install_packages(self, env_name: str, packages: list[str], upgrade: bool = False) -> dict:
        """安装包到虚拟环境"""
        self._validate_packages(packages)
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            python_exe = self._get_python_executable(venv_path)

            args = ["uv", "pip", "install", "--python", python_exe]
            if upgrade:
                args.append("-U")
            args.extend(packages)

            res = await run_command(args, timeout=1800)

            if res.exit_code != 0:
                raise RuntimeError(f"安装包失败: {res.stderr}")

            await self._update_packages_count(env_name)

            logger.info(f"安装包成功: {packages} -> {env_name}")

            return {
                "success": True,
                "installed": packages,
                "output": res.stdout,
            }

    async def uninstall_packages(self, env_name: str, packages: list[str]) -> dict:
        """从虚拟环境卸载包"""
        self._validate_packages(packages)
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            python_exe = self._get_python_executable(venv_path)

            args = ["uv", "pip", "uninstall", "--python", python_exe]
            args.extend(packages)

            res = await run_command(args, timeout=300)

            if res.exit_code != 0:
                raise RuntimeError(f"卸载包失败: {res.stderr}")

            await self._update_packages_count(env_name)

            logger.info(f"卸载包成功: {packages} <- {env_name}")

            return {
                "success": True,
                "uninstalled": packages,
                "output": res.stdout,
            }

    async def list_packages(self, env_name: str) -> list[dict]:
        """列出虚拟环境中已安装的包"""
        venv_path = self._get_venv_path(env_name)

        if not os.path.exists(venv_path):
            raise RuntimeError(f"虚拟环境 {env_name} 不存在")

        python_exe = self._get_python_executable(venv_path)

        res = await run_command(
            ["uv", "pip", "list", "--format", "json", "--python", python_exe],
            timeout=120,
        )

        if res.exit_code != 0:
            raise RuntimeError(f"获取包列表失败: {res.stderr}")
        if not res.stdout.strip():
            raise RuntimeError("获取包列表失败: uv pip list 输出为空")

        try:
            return ujson.loads(res.stdout)
        except ValueError as exc:
            raise RuntimeError("获取包列表失败: JSON 输出无效") from exc

    async def _update_packages_count(self, env_name: str) -> None:
        """更新清单文件中的包数量"""
        venv_path = self._get_venv_path(env_name)
        manifest_path = os.path.join(venv_path, "manifest.json")

        try:
            packages = await self.list_packages(env_name)

            manifest = {}
            if os.path.exists(manifest_path):
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = ujson.load(f)

            manifest["packages_count"] = len(packages)

            with open(manifest_path, "w", encoding="utf-8") as f:
                ujson.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"更新包数量失败: {e}")

    def _validate_packages(self, packages: list[str]) -> None:
        """校验包名格式，防止注入"""
        invalid = [
            package
            for package in packages
            if not package or package.startswith("-") or not PACKAGE_PATTERN.match(package)
        ]
        if invalid:
            raise RuntimeError(f"非法包名: {invalid}")

    def get_platform_info(self) -> dict:
        """获取平台信息（同步版本）"""
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "is_windows": IS_WINDOWS,
            "is_macos": IS_MACOS,
            "is_linux": IS_LINUX,
            "python_version": platform.python_version(),
        }

    async def get_platform_info_async(self) -> dict:
        """获取平台信息（异步版本）"""
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "is_windows": IS_WINDOWS,
            "is_macos": IS_MACOS,
            "is_linux": IS_LINUX,
            "python_version": platform.python_version(),
        }


# 全局实例
uv_manager = UVManager()
