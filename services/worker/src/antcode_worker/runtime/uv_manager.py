"""
UV 运行时管理器

负责虚拟环境的创建、删除、包管理等。
基于 uv 工具实现高效的 Python 环境管理。

支持的操作系统:
- Linux: 完整支持 (mise + local + system)
- macOS: 完整支持 (mise + local + system)
- Windows: 部分支持 (local + system, 不支持 mise)
"""

import asyncio
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime

import ujson
from loguru import logger

# 操作系统检测
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# mise 在 Windows 上不支持
MISE_AVAILABLE = not IS_WINDOWS

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
    """执行命令"""
    final_env = os.environ.copy()
    if env:
        final_env.update(env)

    cmd_str = " ".join(args)
    logger.debug(f"执行命令: {cmd_str}")

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=final_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )

        stdout = stdout_b.decode(errors="ignore") if stdout_b else ""
        stderr = stderr_b.decode(errors="ignore") if stderr_b else ""

        return CommandResult(
            exit_code=process.returncode or 0, stdout=stdout, stderr=stderr
        )
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

    async def _ensure_pip(self, venv_path: str) -> bool:
        """确保虚拟环境中有 pip，如果没有则尝试安装"""
        python_exe = self._get_python_executable(venv_path)

        check_res = await run_command(
            [python_exe, "-m", "pip", "--version"], timeout=10
        )
        if check_res.exit_code == 0:
            return True

        logger.info("虚拟环境缺少 pip，尝试安装...")
        ensurepip_res = await run_command(
            [python_exe, "-m", "ensurepip", "--default-pip"], timeout=120
        )

        if ensurepip_res.exit_code == 0:
            logger.info("pip 安装成功")
            return True

        logger.warning(f"ensurepip 失败，尝试用 uv 安装 pip: {ensurepip_res.stderr}")
        uv_install_pip = await run_command(
            ["uv", "pip", "install", "--python", python_exe, "pip"], timeout=120
        )

        if uv_install_pip.exit_code == 0:
            logger.info("通过 uv 安装 pip 成功")
            return True

        logger.error("无法安装 pip: ensurepip 和 uv 都失败")
        return False

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
                except Exception:
                    pass

            version = manifest.get("python_version", "unknown")
            if version == "unknown":
                res = await run_command([python_exe, "--version"], timeout=10)
                if res.exit_code == 0:
                    version = res.stdout.strip().split()[-1]
                    manifest["python_version"] = version
                    manifest.setdefault("created_at", datetime.now().isoformat())
                    manifest.setdefault("name", name)
                    try:
                        with open(manifest_path, "w", encoding="utf-8") as f:
                            ujson.dump(manifest, f, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

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
                    "scope": scope_value,
                }
            )

        return envs

    async def get_env(self, env_name: str) -> dict | None:
        """获取虚拟环境详情"""
        envs = await self.list_envs()
        return next((e for e in envs if e["name"] == env_name), None)

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

            if python_version:
                python_arg = f"python@{python_version}"
                res = await run_command(
                    ["uv", "venv", venv_path, "--python", python_arg], timeout=600
                )

                if res.exit_code != 0:
                    if MISE_AVAILABLE:
                        res = await run_command(
                            ["mise", "where", f"python@{python_version}"], timeout=60
                        )
                        if res.exit_code == 0:
                            install_dir = res.stdout.strip()
                            python_exe = os.path.join(install_dir, "bin", "python")
                            if os.path.exists(python_exe):
                                res = await run_command(
                                    ["uv", "venv", venv_path, "--python", python_exe],
                                    timeout=600,
                                )

                    if res.exit_code != 0:
                        local_interp = await self._find_local_interpreter(
                            python_version
                        )
                        if local_interp:
                            res = await run_command(
                                ["uv", "venv", venv_path, "--python", local_interp],
                                timeout=600,
                            )

                    if res.exit_code != 0:
                        python_exe = await self._find_python_by_version(python_version)
                        if python_exe:
                            res = await run_command(
                                [python_exe, "-m", "venv", venv_path], timeout=300
                            )

                        if res.exit_code != 0:
                            raise RuntimeError(f"创建虚拟环境失败: {res.stderr}")
            else:
                res = await run_command(
                    ["uv", "venv", venv_path, "--python", sys.executable], timeout=600
                )
                if res.exit_code != 0:
                    res = await run_command(
                        [sys.executable, "-m", "venv", venv_path], timeout=300
                    )
                    if res.exit_code != 0:
                        raise RuntimeError(f"创建虚拟环境失败: {res.stderr}")

            python_exe = self._get_python_executable(venv_path)
            version_res = await run_command([python_exe, "--version"], timeout=10)
            actual_version = (
                version_res.stdout.strip().split()[-1]
                if version_res.exit_code == 0
                else "unknown"
            )

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

    async def install_packages(
        self, env_name: str, packages: list[str], upgrade: bool = False
    ) -> dict:
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
                args = [python_exe, "-m", "pip", "install"]
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

            args = [python_exe, "-m", "pip", "uninstall", "-y"]
            args.extend(packages)

            res = await run_command(args, timeout=300)

            if res.exit_code != 0:
                error_output = res.stderr + res.stdout
                if "No module named pip" in error_output:
                    logger.warning("虚拟环境缺少 pip 模块，使用 uv 卸载")
                else:
                    logger.warning(f"pip uninstall 失败，尝试使用 uv: {error_output}")

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

        if res.exit_code != 0 or not res.stdout.strip():
            res = await run_command(
                [python_exe, "-m", "pip", "list", "--format", "json"], timeout=120
            )

        if res.exit_code != 0:
            raise RuntimeError(f"获取包列表失败: {res.stderr}")

        try:
            return ujson.loads(res.stdout)
        except Exception:
            return []

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
            if not package
            or package.startswith("-")
            or not PACKAGE_PATTERN.match(package)
        ]
        if invalid:
            raise RuntimeError(f"非法包名: {invalid}")

    async def _find_local_interpreter(self, version: str) -> str | None:
        """从本地注册的解释器中查找匹配版本"""
        interpreters = self._load_interpreters()
        for interp in interpreters:
            interp_version = interp.get("version", "")
            if interp_version.startswith(version):
                python_bin = interp.get("python_bin")
                if python_bin and os.path.exists(python_bin):
                    return python_bin
        return None

    async def _find_python_by_version(self, version: str) -> str | None:
        """查找匹配版本的 Python 可执行文件"""
        all_interps = await self.list_all_interpreters()
        for interp in all_interps:
            interp_version = interp.get("version", "")
            if interp_version.startswith(version):
                python_bin = interp.get("python_bin") or interp.get("python_executable")
                if python_bin and os.path.exists(python_bin):
                    return python_bin

        if IS_WINDOWS:
            for minor in range(20, 5, -1):
                if version.startswith(f"3.{minor}"):
                    res = await run_command(
                        [
                            "py",
                            f"-{version[:4]}",
                            "-c",
                            "import sys; sys.stdout.write(sys.executable)",
                        ],
                        timeout=10,
                    )
                    if res.exit_code == 0:
                        return res.stdout.strip()

        return None

    async def list_all_interpreters(self) -> list[dict]:
        """
        列出所有可用的 Python 解释器
        包括: mise 安装的 + 本地注册的 + 系统 Python
        """
        interpreters = []

        try:
            mise_versions = await self.get_installed_python_versions()
            for v in mise_versions:
                if v.get("source") == "mise":
                    interpreters.append({**v, "source": "mise"})
        except Exception as e:
            logger.warning(f"获取 mise 解释器失败: {e}")

        local_interpreters = self._load_interpreters()
        for interp in local_interpreters:
            if os.path.exists(interp.get("python_bin", "")):
                interpreters.append(interp)

        sys_version = (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        )
        interpreters.append(
            {
                "version": sys_version,
                "install_dir": os.path.dirname(sys.executable),
                "python_bin": sys.executable,
                "source": "system",
            }
        )

        return interpreters

    async def install_interpreter(self, version: str) -> dict:
        """安装 Python 解释器（mise）"""
        if not await self.check_mise_available():
            raise RuntimeError("mise 不可用，无法安装解释器")

        res = await run_command(["mise", "install", f"python@{version}"], timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"安装解释器失败: {res.stderr or res.stdout}")

        where_res = await run_command(["mise", "where", f"python@{version}"], timeout=60)
        if where_res.exit_code != 0 or not where_res.stdout.strip():
            raise RuntimeError(f"获取解释器路径失败: {where_res.stderr or where_res.stdout}")

        install_dir = where_res.stdout.strip()
        python_exe = (
            os.path.join(install_dir, "python.exe")
            if IS_WINDOWS
            else os.path.join(install_dir, "bin", "python")
        )

        return {
            "version": version,
            "install_dir": install_dir,
            "python_executable": python_exe,
            "source": "mise",
        }

    async def uninstall_interpreter(self, version: str) -> dict:
        """卸载 Python 解释器（mise）"""
        if not await self.check_mise_available():
            raise RuntimeError("mise 不可用，无法卸载解释器")

        res = await run_command(["mise", "uninstall", f"python@{version}"], timeout=1200)
        if res.exit_code != 0:
            raise RuntimeError(f"卸载解释器失败: {res.stderr or res.stdout}")

        return {"version": version, "removed": True, "source": "mise"}

    async def register_interpreter(self, python_bin: str, version: str | None = None) -> dict:
        """注册本地解释器"""
        if not python_bin or not os.path.exists(python_bin):
            raise RuntimeError("Python 可执行文件不存在")

        if not version:
            res = await run_command([python_bin, "--version"], timeout=10)
            version = res.stdout.strip().split()[-1] if res.exit_code == 0 and res.stdout else "unknown"

        install_dir = os.path.dirname(python_bin)
        interpreters = self._load_interpreters()

        updated = False
        for interp in interpreters:
            if interp.get("python_bin") == python_bin:
                interp.update(
                    {
                        "version": version,
                        "install_dir": install_dir,
                        "python_bin": python_bin,
                        "source": "local",
                    }
                )
                updated = True
                break

        if not updated:
            interpreters.append(
                {
                    "version": version,
                    "install_dir": install_dir,
                    "python_bin": python_bin,
                    "source": "local",
                }
            )

        self._save_interpreters(interpreters)
        return {
            "version": version,
            "install_dir": install_dir,
            "python_bin": python_bin,
            "source": "local",
        }

    async def unregister_interpreter(
        self, python_bin: str | None = None, version: str | None = None
    ) -> dict:
        """取消本地解释器注册"""
        interpreters = self._load_interpreters()
        if not interpreters:
            return {"removed": 0}

        remaining = []
        removed = 0
        for interp in interpreters:
            if python_bin and interp.get("python_bin") == python_bin:
                removed += 1
                continue
            if version and interp.get("version", "").startswith(version):
                removed += 1
                continue
            remaining.append(interp)

        self._save_interpreters(remaining)
        return {"removed": removed}

    async def get_installed_python_versions(self) -> list[dict]:
        """
        获取已安装的 Python 版本列表

        - Linux/macOS: mise 安装的 + 系统 Python
        - Windows: 仅系统 Python（不支持 mise）
        """
        versions = []

        if MISE_AVAILABLE:
            res = await run_command(
                ["mise", "ls", "--installed", "python"], timeout=60
            )
            if res.exit_code == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    ver = s.split()[0]

                    where_res = await run_command(
                        ["mise", "where", f"python@{ver}"], timeout=30
                    )
                    if where_res.exit_code == 0:
                        install_dir = where_res.stdout.strip()
                        if IS_WINDOWS:
                            python_exe = os.path.join(install_dir, "python.exe")
                        else:
                            python_exe = os.path.join(install_dir, "bin", "python")

                        if os.path.exists(python_exe):
                            versions.append(
                                {
                                    "version": ver,
                                    "install_dir": install_dir,
                                    "python_executable": python_exe,
                                    "source": "mise",
                                }
                            )

        if IS_WINDOWS:
            await self._find_windows_pythons(versions)

        sys_version = (
            f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        )
        versions.append(
            {
                "version": f"{sys_version} (local)",
                "install_dir": os.path.dirname(sys.executable),
                "python_executable": sys.executable,
                "source": "system",
            }
        )

        return versions

    async def _find_windows_pythons(self, versions: list[dict]) -> None:
        """在 Windows 上查找已安装的 Python"""
        pyenv_root = os.environ.get("PYENV_ROOT") or os.path.expanduser(
            "~\\.pyenv\\pyenv-win"
        )
        if os.path.exists(pyenv_root):
            versions_dir = os.path.join(pyenv_root, "versions")
            if os.path.exists(versions_dir):
                for ver_dir in os.listdir(versions_dir):
                    ver_path = os.path.join(versions_dir, ver_dir)
                    python_exe = os.path.join(ver_path, "python.exe")
                    if os.path.exists(python_exe):
                        versions.append(
                            {
                                "version": ver_dir,
                                "install_dir": ver_path,
                                "python_executable": python_exe,
                                "source": "pyenv-win",
                            }
                        )

        local_programs = os.path.expanduser("~\\AppData\\Local\\Programs\\Python")
        if os.path.exists(local_programs):
            for item in os.listdir(local_programs):
                item_path = os.path.join(local_programs, item)
                python_exe = os.path.join(item_path, "python.exe")
                if os.path.exists(python_exe):
                    res = await run_command([python_exe, "--version"], timeout=10)
                    if res.exit_code == 0:
                        version = res.stdout.strip().split()[-1]
                        versions.append(
                            {
                                "version": version,
                                "install_dir": item_path,
                                "python_executable": python_exe,
                                "source": "local",
                            }
                        )

    def _get_interpreters_file(self) -> str:
        """获取本地解释器配置文件路径"""
        config_dir = os.path.dirname(self.venvs_dir) if self.venvs_dir else os.path.expanduser("~/.antcode")

        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "local_interpreters.json")

    def _load_interpreters(self) -> list[dict]:
        """加载本地注册的解释器列表"""
        file_path = self._get_interpreters_file()
        if not os.path.exists(file_path):
            return []

        try:
            with open(file_path, encoding="utf-8") as f:
                return ujson.load(f)
        except Exception:
            return []

    def _save_interpreters(self, interpreters: list[dict]) -> None:
        """保存本地注册的解释器列表"""
        file_path = self._get_interpreters_file()
        with open(file_path, "w", encoding="utf-8") as f:
            ujson.dump(interpreters, f, ensure_ascii=False, indent=2)

    async def check_mise_available(self) -> bool:
        """检查mise是否真正可用"""
        if IS_WINDOWS:
            return False
        try:
            result = await run_command(["mise", "--version"], timeout=10)
            return result.exit_code == 0
        except Exception:
            return False

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
            "mise_available": False,
            "python_version": platform.python_version(),
        }

    async def get_platform_info_async(self) -> dict:
        """获取平台信息（异步版本，包含mise真实可用性检查）"""
        mise_available = await self.check_mise_available()
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "is_windows": IS_WINDOWS,
            "is_macos": IS_MACOS,
            "is_linux": IS_LINUX,
            "mise_available": mise_available,
            "python_version": platform.python_version(),
        }


# 全局实例
uv_manager = UVManager()
