"""
本地环境管理服务
负责虚拟环境的创建、删除、包管理等

支持的操作系统:
- Linux: 完整支持 (mise + local + system)
- macOS: 完整支持 (mise + local + system)
- Windows: 部分支持 (local + system, 不支持 mise)
"""
import asyncio
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

import ujson
from loguru import logger


# 操作系统检测
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# mise 在 Windows 上不支持
MISE_AVAILABLE = not IS_WINDOWS


@dataclass
class CommandResult:
    """命令执行结果"""
    exit_code: int
    stdout: str
    stderr: str


async def run_command(
    args: List[str],
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 900
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
            process.communicate(),
            timeout=timeout
        )

        stdout = stdout_b.decode(errors="ignore") if stdout_b else ""
        stderr = stderr_b.decode(errors="ignore") if stderr_b else ""

        return CommandResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr
        )
    except asyncio.TimeoutError:
        return CommandResult(
            exit_code=124,
            stdout="",
            stderr=f"命令超时: {cmd_str}"
        )
    except FileNotFoundError:
        return CommandResult(
            exit_code=127,
            stdout="",
            stderr=f"命令未找到: {args[0]}"
        )
    except Exception as e:
        return CommandResult(
            exit_code=-1,
            stdout="",
            stderr=str(e)
        )


class LocalEnvService:
    """本地环境管理服务"""

    def __init__(self, venvs_dir: Optional[str] = None):
        self.venvs_dir = venvs_dir
        self._locks: Dict[str, asyncio.Lock] = {}
        self._env_count_cache: int = 0

    def set_venvs_dir(self, venvs_dir: str):
        """设置虚拟环境目录"""
        self.venvs_dir = venvs_dir
        os.makedirs(venvs_dir, exist_ok=True)
        # 初始化时更新环境数量缓存
        self._update_env_count_cache()

    def _update_env_count_cache(self):
        """更新环境数量缓存（同步方法）"""
        if not self.venvs_dir or not os.path.exists(self.venvs_dir):
            self._env_count_cache = 0
            return

        count = 0
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if os.path.isdir(venv_path):
                # 简单检查是否是有效的虚拟环境
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
            candidates = [
                os.path.join(venv_path, "Scripts", "python.exe"),
            ]
        else:
            candidates = [
                os.path.join(venv_path, "bin", "python"),
                os.path.join(venv_path, "bin", "python3"),
            ]
        return next((p for p in candidates if os.path.exists(p)), candidates[0])

    async def _ensure_pip(self, venv_path: str) -> bool:
        """确保虚拟环境中有 pip，如果没有则尝试安装"""
        python_exe = self._get_python_executable(venv_path)

        # 检查 pip 是否存在
        check_res = await run_command([python_exe, "-m", "pip", "--version"], timeout=10)
        if check_res.exit_code == 0:
            return True

        # pip 不存在，尝试使用 ensurepip 安装
        logger.info(f"虚拟环境缺少 pip，尝试安装...")
        ensurepip_res = await run_command([python_exe, "-m", "ensurepip", "--default-pip"], timeout=120)

        if ensurepip_res.exit_code == 0:
            logger.info("pip 安装成功")
            return True

        # 如果 ensurepip 也失败，尝试通过 uv 安装 pip
        logger.warning(f"ensurepip 失败，尝试用 uv 安装 pip: {ensurepip_res.stderr}")
        uv_install_pip = await run_command(
            ["uv", "pip", "install", "--python", python_exe, "pip"],
            timeout=120
        )

        if uv_install_pip.exit_code == 0:
            logger.info("通过 uv 安装 pip 成功")
            return True

        logger.error(f"无法安装 pip: ensurepip 和 uv 都失败")
        return False

    async def list_envs(self) -> List[Dict[str, Any]]:
        """列出所有虚拟环境"""
        if not self.venvs_dir or not os.path.exists(self.venvs_dir):
            return []

        envs = []
        for name in os.listdir(self.venvs_dir):
            venv_path = os.path.join(self.venvs_dir, name)
            if not os.path.isdir(venv_path):
                continue

            # 检查是否是有效的虚拟环境
            python_exe = self._get_python_executable(venv_path)
            if not os.path.exists(python_exe):
                continue

            # 读取清单文件
            manifest_path = os.path.join(venv_path, "manifest.json")
            manifest = {}
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = ujson.load(f)
                except Exception:
                    pass

            # 获取 Python 版本
            version = manifest.get("python_version", "unknown")
            if version == "unknown":
                res = await run_command([python_exe, "--version"], timeout=10)
                if res.exit_code == 0:
                    version = res.stdout.strip().split()[-1]

            envs.append({
                "name": name,
                "path": venv_path,
                "python_version": version,
                "python_executable": python_exe,
                "created_at": manifest.get("created_at"),
                "created_by": manifest.get("created_by"),
                "packages_count": manifest.get("packages_count", 0),
            })

        return envs

    async def get_env(self, env_name: str) -> Optional[Dict[str, Any]]:
        """获取虚拟环境详情"""
        envs = await self.list_envs()
        return next((e for e in envs if e["name"] == env_name), None)

    async def create_env(
        self,
        env_name: str,
        python_version: Optional[str] = None,
        packages: Optional[List[str]] = None,
        created_by: Optional[str] = None,
    ) -> Dict[str, Any]:
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

            # 检查是否已存在
            if os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 已存在")

            # 确定 Python 可执行文件
            if python_version:
                # 尝试使用 uv 指定版本
                python_arg = f"python@{python_version}"

                # 先尝试用 uv 创建
                res = await run_command(
                    ["uv", "venv", venv_path, "--python", python_arg],
                    timeout=600
                )

                if res.exit_code != 0:
                    # 回退到使用 mise 安装的 Python（仅 Linux/macOS）
                    if MISE_AVAILABLE:
                        res = await run_command(
                            ["mise", "where", f"python@{python_version}"],
                            timeout=60
                        )
                        if res.exit_code == 0:
                            install_dir = res.stdout.strip()
                            python_exe = os.path.join(install_dir, "bin", "python")
                            if os.path.exists(python_exe):
                                res = await run_command(
                                    ["uv", "venv", venv_path, "--python", python_exe],
                                    timeout=600
                                )

                    # 尝试从本地注册的解释器中查找
                    if res.exit_code != 0:
                        local_interp = await self._find_local_interpreter(python_version)
                        if local_interp:
                            res = await run_command(
                                ["uv", "venv", venv_path, "--python", local_interp],
                                timeout=600
                            )

                    # 最后回退到标准 venv
                    if res.exit_code != 0:
                        # 尝试找到匹配版本的 Python
                        python_exe = await self._find_python_by_version(python_version)
                        if python_exe:
                            res = await run_command(
                                [python_exe, "-m", "venv", venv_path],
                                timeout=300
                            )

                        if res.exit_code != 0:
                            raise RuntimeError(f"创建虚拟环境失败: {res.stderr}")
            else:
                # 使用当前 Python
                res = await run_command(
                    ["uv", "venv", venv_path, "--python", sys.executable],
                    timeout=600
                )
                if res.exit_code != 0:
                    # 回退到标准 venv
                    res = await run_command(
                        [sys.executable, "-m", "venv", venv_path],
                        timeout=300
                    )
                    if res.exit_code != 0:
                        raise RuntimeError(f"创建虚拟环境失败: {res.stderr}")

            # 获取实际的 Python 版本
            python_exe = self._get_python_executable(venv_path)
            version_res = await run_command([python_exe, "--version"], timeout=10)
            actual_version = version_res.stdout.strip().split()[-1] if version_res.exit_code == 0 else "unknown"

            # 写入清单文件
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

            # 安装初始包
            if packages:
                await self.install_packages(env_name, packages)

            # 更新包数量（包含默认安装的 pip 等）
            await self._update_packages_count(env_name)

            # 重新读取更新后的 manifest
            with open(manifest_path, "r", encoding="utf-8") as f:
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
        self,
        env_name: str,
        packages: List[str],
        upgrade: bool = False
    ) -> Dict[str, Any]:
        """安装包到虚拟环境"""
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            python_exe = self._get_python_executable(venv_path)

            # 构建安装命令
            args = ["uv", "pip", "install", "--python", python_exe]
            if upgrade:
                args.append("-U")
            args.extend(packages)

            res = await run_command(args, timeout=1800)

            if res.exit_code != 0:
                # 回退到 pip
                args = [python_exe, "-m", "pip", "install"]
                if upgrade:
                    args.append("-U")
                args.extend(packages)
                res = await run_command(args, timeout=1800)

            if res.exit_code != 0:
                raise RuntimeError(f"安装包失败: {res.stderr}")

            # 更新包数量
            await self._update_packages_count(env_name)

            logger.info(f"安装包成功: {packages} -> {env_name}")

            return {
                "success": True,
                "installed": packages,
                "output": res.stdout,
            }

    async def uninstall_packages(
        self,
        env_name: str,
        packages: List[str]
    ) -> Dict[str, Any]:
        """从虚拟环境卸载包"""
        lock = self._get_lock(f"env:{env_name}")
        async with lock:
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            python_exe = self._get_python_executable(venv_path)

            # 优先使用 pip uninstall
            args = [python_exe, "-m", "pip", "uninstall", "-y"]
            args.extend(packages)

            res = await run_command(args, timeout=300)

            # 如果 pip 不存在或失败，回退到 uv
            if res.exit_code != 0:
                # 检查是否是因为缺少 pip (在 stdout 或 stderr 中)
                error_output = res.stderr + res.stdout
                if "No module named pip" in error_output:
                    logger.warning(f"虚拟环境缺少 pip 模块，使用 uv 卸载")
                else:
                    logger.warning(f"pip uninstall 失败，尝试使用 uv: {error_output}")

                # 使用 uv pip uninstall (uv 不需要 -y 参数)
                args = ["uv", "pip", "uninstall", "--python", python_exe]
                args.extend(packages)
                res = await run_command(args, timeout=300)

            if res.exit_code != 0:
                raise RuntimeError(f"卸载包失败: {res.stderr}")

            # 更新包数量
            await self._update_packages_count(env_name)

            logger.info(f"卸载包成功: {packages} <- {env_name}")

            return {
                "success": True,
                "uninstalled": packages,
                "output": res.stdout,
            }

    async def list_packages(self, env_name: str) -> List[Dict[str, str]]:
        """列出虚拟环境中已安装的包"""
        venv_path = self._get_venv_path(env_name)

        if not os.path.exists(venv_path):
            raise RuntimeError(f"虚拟环境 {env_name} 不存在")

        python_exe = self._get_python_executable(venv_path)

        # 使用 uv pip list
        res = await run_command(
            ["uv", "pip", "list", "--format", "json", "--python", python_exe],
            timeout=120
        )

        if res.exit_code != 0 or not res.stdout.strip():
            # 回退到 pip list
            res = await run_command(
                [python_exe, "-m", "pip", "list", "--format", "json"],
                timeout=120
            )

        if res.exit_code != 0:
            raise RuntimeError(f"获取包列表失败: {res.stderr}")

        try:
            return ujson.loads(res.stdout)
        except Exception:
            return []

    async def _update_packages_count(self, env_name: str):
        """更新清单文件中的包数量"""
        venv_path = self._get_venv_path(env_name)
        manifest_path = os.path.join(venv_path, "manifest.json")

        try:
            packages = await self.list_packages(env_name)

            manifest = {}
            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = ujson.load(f)

            manifest["packages_count"] = len(packages)

            with open(manifest_path, "w", encoding="utf-8") as f:
                ujson.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"更新包数量失败: {e}")

    async def get_available_python_versions(self) -> List[str]:
        """
        获取可用的 Python 版本列表
        
        - Linux/macOS: 通过 mise 获取可安装版本
        - Windows: 返回空列表（不支持 mise）
        """
        versions = []

        # Windows 不支持 mise
        if IS_WINDOWS:
            logger.info("Windows 系统不支持 mise，请手动安装 Python 后使用本地注册功能")
            return []

        # 尝试通过 mise 获取
        res = await run_command(["mise", "ls-remote", "python"], timeout=120)
        if res.exit_code == 0:
            for line in res.stdout.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    # 只保留主要版本（3.x.y 格式）
                    parts = s.split(".")
                    if len(parts) >= 2 and parts[0].isdigit():
                        versions.append(s.split()[0])
        else:
            logger.warning(f"mise 不可用或列表失败: {res.stderr}")

        # 过滤出最近的版本
        filtered = []
        seen_minors = set()
        for v in reversed(versions):
            parts = v.split(".")
            if len(parts) >= 2:
                minor = f"{parts[0]}.{parts[1]}"
                if minor not in seen_minors:
                    seen_minors.add(minor)
                    filtered.append(v)

        return list(reversed(filtered))[-10:]  # 返回最近10个版本

    async def get_installed_python_versions(self) -> List[Dict[str, str]]:
        """
        获取已安装的 Python 版本列表
        
        - Linux/macOS: mise 安装的 + 系统 Python
        - Windows: 仅系统 Python（不支持 mise）
        """
        versions = []

        # 通过 mise 获取（仅 Linux/macOS）
        if MISE_AVAILABLE:
            res = await run_command(["mise", "ls", "--installed", "python"], timeout=60)
            if res.exit_code == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    ver = s.split()[0]

                    # 获取安装路径
                    where_res = await run_command(["mise", "where", f"python@{ver}"], timeout=30)
                    if where_res.exit_code == 0:
                        install_dir = where_res.stdout.strip()
                        # 跨平台路径处理
                        if IS_WINDOWS:
                            python_exe = os.path.join(install_dir, "python.exe")
                        else:
                            python_exe = os.path.join(install_dir, "bin", "python")

                        if os.path.exists(python_exe):
                            versions.append({
                                "version": ver,
                                "install_dir": install_dir,
                                "python_executable": python_exe,
                                "source": "mise",
                            })

        # Windows: 尝试查找常见的 Python 安装位置
        if IS_WINDOWS:
            await self._find_windows_pythons(versions)

        # 添加系统 Python
        sys_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        versions.append({
            "version": f"{sys_version} (system)",
            "install_dir": os.path.dirname(sys.executable),
            "python_executable": sys.executable,
            "source": "system",
        })

        return versions

    async def _find_windows_pythons(self, versions: List[Dict[str, str]]):
        """在 Windows 上查找已安装的 Python"""
        # 常见的 Windows Python 安装位置
        search_paths = [
            os.path.expanduser("~\\AppData\\Local\\Programs\\Python"),
            "C:\\Python39",
            "C:\\Python310",
            "C:\\Python311",
            "C:\\Python312",
            "C:\\Program Files\\Python39",
            "C:\\Program Files\\Python310",
            "C:\\Program Files\\Python311",
            "C:\\Program Files\\Python312",
        ]

        # 添加 pyenv-win 路径
        pyenv_root = os.environ.get("PYENV_ROOT") or os.path.expanduser("~\\.pyenv\\pyenv-win")
        if os.path.exists(pyenv_root):
            versions_dir = os.path.join(pyenv_root, "versions")
            if os.path.exists(versions_dir):
                for ver_dir in os.listdir(versions_dir):
                    ver_path = os.path.join(versions_dir, ver_dir)
                    python_exe = os.path.join(ver_path, "python.exe")
                    if os.path.exists(python_exe):
                        versions.append({
                            "version": ver_dir,
                            "install_dir": ver_path,
                            "python_executable": python_exe,
                            "source": "pyenv-win",
                        })

        # 查找 AppData 下的 Python
        local_programs = os.path.expanduser("~\\AppData\\Local\\Programs\\Python")
        if os.path.exists(local_programs):
            for item in os.listdir(local_programs):
                item_path = os.path.join(local_programs, item)
                python_exe = os.path.join(item_path, "python.exe")
                if os.path.exists(python_exe):
                    # 获取版本
                    res = await run_command([python_exe, "--version"], timeout=10)
                    if res.exit_code == 0:
                        version = res.stdout.strip().split()[-1]
                        versions.append({
                            "version": version,
                            "install_dir": item_path,
                            "python_executable": python_exe,
                            "source": "local",
                        })

    async def install_python_version(self, version: str) -> Dict[str, Any]:
        """
        安装指定版本的 Python
        
        - Linux/macOS: 通过 mise 安装
        - Windows: 不支持自动安装，请手动安装后使用注册功能
        """
        # Windows 不支持 mise 安装
        if IS_WINDOWS:
            raise RuntimeError(
                f"Windows 系统不支持通过 mise 安装 Python。\n"
                f"请手动安装 Python {version}，然后使用 /interpreters/register 接口注册。\n"
                f"推荐下载地址: https://www.python.org/downloads/release/python-{version.replace('.', '')}/"
            )

        lock = self._get_lock(f"python:{version}")
        async with lock:
            # 检查 mise 是否可用
            mise_check = await run_command(["mise", "--version"], timeout=10)
            if mise_check.exit_code != 0:
                raise RuntimeError(
                    "mise 未安装或不可用。请先安装 mise: https://mise.jdx.dev/getting-started.html"
                )

            res = await run_command(
                ["mise", "install", "-y", f"python@{version}"],
                timeout=1800
            )

            if res.exit_code != 0:
                raise RuntimeError(f"安装 Python {version} 失败: {res.stderr}")

            # 获取安装路径
            where_res = await run_command(
                ["mise", "where", f"python@{version}"],
                timeout=60
            )

            if where_res.exit_code != 0:
                raise RuntimeError(f"查找 Python {version} 路径失败")

            install_dir = where_res.stdout.strip()
            python_exe = os.path.join(install_dir, "bin", "python")

            logger.info(f"Python {version} 安装成功: {install_dir}")

            return {
                "version": version,
                "install_dir": install_dir,
                "python_executable": python_exe,
                "source": "mise",
            }

    async def register_local_interpreter(self, python_bin: str) -> Dict[str, Any]:
        """
        注册本地 Python 解释器
        
        Args:
            python_bin: Python 可执行文件的完整路径
        
        Returns:
            解释器信息
        """
        # 检查路径是否存在
        if not os.path.exists(python_bin):
            raise RuntimeError(f"Python 路径不存在: {python_bin}")

        # 获取版本信息
        res = await run_command([python_bin, "--version"], timeout=10)
        if res.exit_code != 0:
            raise RuntimeError(f"无法获取 Python 版本: {res.stderr}")

        # 解析版本 "Python 3.12.0" -> "3.12.0"
        version_output = res.stdout.strip() or res.stderr.strip()
        version = version_output.split()[-1] if version_output else "unknown"

        # 获取安装目录
        install_dir = os.path.dirname(os.path.dirname(python_bin))

        # 保存到本地配置文件
        interpreters_file = self._get_interpreters_file()
        interpreters = self._load_interpreters()

        # 检查是否已注册
        existing = next((i for i in interpreters if i.get("python_bin") == python_bin), None)
        if existing:
            return existing

        interpreter_info = {
            "id": f"local_{len(interpreters) + 1}",
            "version": version,
            "install_dir": install_dir,
            "python_bin": python_bin,
            "source": "local",
            "registered_at": datetime.now().isoformat(),
        }

        interpreters.append(interpreter_info)
        self._save_interpreters(interpreters)

        logger.info(f"本地解释器注册成功: {python_bin} (Python {version})")

        return interpreter_info

    async def unregister_local_interpreter(self, python_bin: str) -> bool:
        """取消注册本地解释器"""
        interpreters = self._load_interpreters()
        original_count = len(interpreters)

        interpreters = [i for i in interpreters if i.get("python_bin") != python_bin]

        if len(interpreters) < original_count:
            self._save_interpreters(interpreters)
            logger.info(f"本地解释器已取消注册: {python_bin}")
            return True

        return False

    async def list_all_interpreters(self) -> List[Dict[str, Any]]:
        """
        列出所有可用的 Python 解释器
        包括: mise 安装的 + 本地注册的 + 系统 Python
        """
        interpreters = []

        # 1. mise 安装的解释器
        try:
            mise_versions = await self.get_installed_python_versions()
            for v in mise_versions:
                if "(system)" not in v.get("version", ""):
                    interpreters.append({
                        **v,
                        "source": "mise",
                    })
        except Exception as e:
            logger.warning(f"获取 mise 解释器失败: {e}")

        # 2. 本地注册的解释器
        local_interpreters = self._load_interpreters()
        for interp in local_interpreters:
            # 验证路径是否仍然有效
            if os.path.exists(interp.get("python_bin", "")):
                interpreters.append(interp)

        # 3. 系统 Python
        sys_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        interpreters.append({
            "version": sys_version,
            "install_dir": os.path.dirname(sys.executable),
            "python_bin": sys.executable,
            "source": "system",
        })

        return interpreters

    def _get_interpreters_file(self) -> str:
        """获取本地解释器配置文件路径"""
        if self.venvs_dir:
            config_dir = os.path.dirname(self.venvs_dir)
        else:
            config_dir = os.path.expanduser("~/.antcode")

        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "local_interpreters.json")

    def _load_interpreters(self) -> List[Dict[str, Any]]:
        """加载本地注册的解释器列表"""
        file_path = self._get_interpreters_file()
        if not os.path.exists(file_path):
            return []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return ujson.load(f)
        except Exception:
            return []

    def _save_interpreters(self, interpreters: List[Dict[str, Any]]):
        """保存本地注册的解释器列表"""
        file_path = self._get_interpreters_file()
        with open(file_path, "w", encoding="utf-8") as f:
            ujson.dump(interpreters, f, ensure_ascii=False, indent=2)

    async def _find_local_interpreter(self, version: str) -> Optional[str]:
        """从本地注册的解释器中查找匹配版本"""
        interpreters = self._load_interpreters()
        for interp in interpreters:
            interp_version = interp.get("version", "")
            if interp_version.startswith(version):
                python_bin = interp.get("python_bin")
                if python_bin and os.path.exists(python_bin):
                    return python_bin
        return None

    async def _find_python_by_version(self, version: str) -> Optional[str]:
        """查找匹配版本的 Python 可执行文件"""
        # 先从所有解释器中查找
        all_interps = await self.list_all_interpreters()
        for interp in all_interps:
            interp_version = interp.get("version", "")
            if interp_version.startswith(version):
                python_bin = interp.get("python_bin") or interp.get("python_executable")
                if python_bin and os.path.exists(python_bin):
                    return python_bin

        # Windows: 尝试常见的命令
        if IS_WINDOWS:
            # 尝试 py launcher
            for minor in range(20, 5, -1):
                if version.startswith(f"3.{minor}"):
                    py_cmd = f"py -{version[:4]}"
                    res = await run_command(["py", f"-{version[:4]}", "-c", "import sys; print(sys.executable)"], timeout=10)
                    if res.exit_code == 0:
                        return res.stdout.strip()

        return None

    async def check_mise_available(self) -> bool:
        """检查mise是否真正可用"""
        if IS_WINDOWS:
            return False
        try:
            result = await run_command(["mise", "--version"], timeout=10)
            return result.exit_code == 0
        except Exception:
            return False

    def get_platform_info(self) -> Dict[str, Any]:
        """获取平台信息（同步版本，mise_available需要异步检查）"""
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "is_windows": IS_WINDOWS,
            "is_macos": IS_MACOS,
            "is_linux": IS_LINUX,
            "mise_available": False,  # 默认False，需要异步检查
            "python_version": platform.python_version(),
        }

    async def get_platform_info_async(self) -> Dict[str, Any]:
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

    def clear_interpreters(self) -> Dict[str, Any]:
        """
        清除所有本地注册的解释器数据
        
        Returns:
            清除结果，包含删除的数量
        """
        interpreters = self._load_interpreters()
        count = len(interpreters)

        # 清空解释器列表
        self._save_interpreters([])

        logger.info(f"已清除 {count} 个本地解释器记录")

        return {
            "cleared": True,
            "count": count,
        }


# 全局实例
local_env_service = LocalEnvService()

