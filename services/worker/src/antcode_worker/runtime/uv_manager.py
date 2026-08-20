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
import contextlib
import os
import platform
import re
import shutil
import signal
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import ujson
from antcode_contracts.runtime_control_errors import RuntimeEnvAlreadyExistsError
from antcode_contracts.runtime_metadata import validate_runtime_creator, validate_runtime_metadata
from loguru import logger

from antcode_worker.runtime.manifest import (
    load_runtime_manifest,
    runtime_manifest_creator,
    runtime_manifest_metadata,
    write_runtime_manifest,
)

# 操作系统检测
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# P0-01：严格 PEP 508 name[extras] operator version [; markers] 白名单。
# 之前的正则允许 `pkg@https://…`、`git+https://…`、`https://…tar.gz`、`file:///…` 等
# direct-reference / VCS / URL / 本地路径，会让 uv pip install 从任意来源拉取并执行
# sdist 的 setup.py / PEP 517 build backend / VCS post-install hook，等价于以 Worker
# 主进程 UID 的 RCE 面。
# 新正则只允许：字母/数字打头 + 后续 [A-Za-z0-9._\-\[\],<>=!~;()*+'" ]。
# 显式禁止： ':' '/' '@' '#' '$' 反引号 反斜杠 换行 等 URL/scheme/shell 元字符。
# 满足常见需求：requests、requests==2.31.0、urllib3>=1.26,<2、pkg[extras]==1.0、
# pkg==1.0; python_version>='3.10'。
PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],<>=!~;()*+'\" ]*$")

# P0-01：反模式黑名单，命中任一直接拒绝（比正则更保险）。
_FORBIDDEN_PACKAGE_SUBSTRINGS = (
    "://",  # http:// https:// file:// 等 URL scheme
    "git+",
    "hg+",
    "svn+",
    "bzr+",
    "@http",
    "@ftp",
    "@file",
    "@git",
    "@/",  # @/local/path
    "..",  # 路径穿越
    "`",  # 命令注入元字符
    "$",  # shell 变量替换
    "\\",  # 反斜杠
    "\x00",
    "\n",
    "\r",
    "\t",
)

# 环境名白名单：字母/数字/点/下划线/连字符，长度 1-64
# 严格限制以防止路径遍历攻击 (../.. 等)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PYTHON_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")


def _validate_env_name(name: str) -> str:
    """
    校验环境名合法性，防止路径遍历攻击。

    合法的 env_name 必须只包含 [A-Za-z0-9._-]，长度 1-64。
    禁止 "." / ".." / 空字符串 / 含分隔符或空白 / 超长字符串。
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"非法环境名: {name!r}")
    if name in {".", ".."}:
        raise ValueError(f"非法环境名: {name!r}")
    if not _ENV_NAME_PATTERN.match(name):
        raise ValueError(f"非法环境名: {name!r}")
    return name


def _normalize_python_version(version: str) -> str:
    normalized = version.strip()
    if not _PYTHON_VERSION_PATTERN.fullmatch(normalized):
        raise RuntimeError(f"非法 Python 版本: {version!r}")
    return normalized


@dataclass
class CommandResult:
    """命令执行结果"""

    exit_code: int
    stdout: str
    stderr: str


async def _terminate_command_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if IS_WINDOWS:
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await asyncio.wait_for(process.wait(), timeout=5)


async def run_command(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict | None = None,
    timeout: int = 900,
    inherit_env: bool = True,
    max_output_bytes: int = 8 * 1024 * 1024,
) -> CommandResult:
    """执行命令。argv[0] 会经 shutil.which 解析（Windows 上匹配 .cmd/.exe/.bat）。"""
    from antcode_worker.runtime.command_logging import format_command_for_log
    from antcode_worker.runtime.win_exec import resolve_argv

    final_env = os.environ.copy() if inherit_env else {}
    if env:
        final_env.update(env)

    # Windows-safe: 把 argv[0] 换成绝对路径（npm→npm.cmd 等）；Unix 也走此逻辑保证一致
    resolved_args = resolve_argv(args)
    cmd_str = format_command_for_log(resolved_args)
    logger.debug(f"执行命令: {cmd_str}")

    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *resolved_args,
            cwd=cwd,
            env=final_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=not IS_WINDOWS,
        )

        output_budget = [0]
        stdout_b, stderr_b = await asyncio.wait_for(
            asyncio.gather(
                _read_bounded_stream(process.stdout, max_output_bytes, output_budget),
                _read_bounded_stream(process.stderr, max_output_bytes, output_budget),
            ),
            timeout=timeout,
        )
        await process.wait()

        stdout = stdout_b.decode() if stdout_b else ""
        stderr = stderr_b.decode() if stderr_b else ""

        return CommandResult(exit_code=process.returncode or 0, stdout=stdout, stderr=stderr)
    except TimeoutError:
        if process is not None:
            await _terminate_command_process(process)
        return CommandResult(exit_code=124, stdout="", stderr=f"命令超时: {cmd_str}")
    except CommandOutputTooLargeError as exc:
        if process is not None:
            await _terminate_command_process(process)
        return CommandResult(exit_code=125, stdout="", stderr=str(exc))
    except asyncio.CancelledError:
        if process is not None:
            await _terminate_command_process(process)
        raise
    except FileNotFoundError:
        return CommandResult(exit_code=127, stdout="", stderr=f"命令未找到: {args[0]}")
    except Exception as e:
        return CommandResult(exit_code=-1, stdout="", stderr=str(e))


class CommandOutputTooLargeError(RuntimeError):
    pass


async def _read_bounded_stream(
    stream: asyncio.StreamReader | None,
    max_output_bytes: int,
    output_budget: list[int],
) -> bytes:
    if stream is None:
        return b""
    content = bytearray()
    while chunk := await stream.read(64 * 1024):
        output_budget[0] += len(chunk)
        if output_budget[0] > max_output_bytes:
            raise CommandOutputTooLargeError(f"命令输出超过上限 {max_output_bytes} 字节")
        content.extend(chunk)
    return bytes(content)


class UVManager:
    """
    UV 运行时管理器

    使用 uv 工具管理 Python 虚拟环境。
    """

    def __init__(self, venvs_dir: str | None = None):
        self.venvs_dir = venvs_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}
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

    @contextlib.asynccontextmanager
    async def _env_operation(self, key: str):
        """按 key 串行操作，并在最后一个等待者退出时回收锁。"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        lock = self._locks[key]
        self._lock_users[key] = self._lock_users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            users = self._lock_users.get(key, 0) - 1
            if users > 0:
                self._lock_users[key] = users
            else:
                self._lock_users.pop(key, None)
                if not lock.locked() and self._locks.get(key) is lock:
                    self._locks.pop(key, None)

    def _get_venv_path(self, env_name: str) -> str:
        """获取虚拟环境路径（含名称白名单 + realpath 越界校验）"""
        if not self.venvs_dir:
            raise RuntimeError("venvs_dir 未设置")

        _validate_env_name(env_name)

        base = Path(self.venvs_dir).resolve()
        target = (base / env_name).resolve()
        # env_name 必须解析为 venvs_dir 的直接子目录，防止符号链接或异常拼接越界
        if target.parent != base:
            raise ValueError(f"非法环境路径（越界）: {env_name!r}")

        return str(target)

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
            manifest = load_runtime_manifest(manifest_path)
            key, description = runtime_manifest_metadata(manifest)
            created_by, owner_user_id = runtime_manifest_creator(manifest)

            version = manifest.get("python_version", "unknown")
            if version == "unknown":
                res = await run_command([python_exe, "--version"], timeout=10)
                if res.exit_code == 0:
                    version = res.stdout.strip().split()[-1]
                    manifest["python_version"] = version
                    manifest.setdefault("created_at", datetime.now().isoformat())
                    manifest.setdefault("name", name)
                    write_runtime_manifest(manifest_path, manifest)

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
                    "created_by": created_by,
                    "owner_user_id": owner_user_id,
                    "packages_count": manifest.get("packages_count", 0),
                    "key": key,
                    "description": description,
                    "scope": scope_value,
                }
            )

        return envs

    async def get_env(self, env_name: str) -> dict | None:
        """获取虚拟环境详情"""
        _validate_env_name(env_name)
        envs = await self.list_envs()
        return next((e for e in envs if e["name"] == env_name), None)

    async def update_env(
        self,
        env_name: str,
        key: str | None = None,
        description: str | None = None,
    ) -> dict:
        """更新虚拟环境元数据（manifest）"""
        _validate_env_name(env_name)
        normalized_key, normalized_desc = validate_runtime_metadata(key, description)
        async with self._env_operation(f"env:{env_name}"):
            venv_path = self._get_venv_path(env_name)
            if not os.path.exists(venv_path):
                raise RuntimeError(f"虚拟环境 {env_name} 不存在")

            manifest_path = os.path.join(venv_path, "manifest.json")
            manifest = load_runtime_manifest(manifest_path)
            runtime_manifest_metadata(manifest)
            runtime_manifest_creator(manifest)

            manifest.setdefault("name", env_name)
            manifest.setdefault("created_at", datetime.now().isoformat())

            if key is None or not normalized_key:
                manifest.pop("key", None)
            else:
                manifest["key"] = normalized_key

            if description is None or not normalized_desc:
                manifest.pop("description", None)
            else:
                manifest["description"] = normalized_desc

            write_runtime_manifest(manifest_path, manifest)

            env_data = await self.get_env(env_name)
            if not env_data:
                raise RuntimeError("环境更新后读取失败")

            return env_data

    async def create_env(
        self,
        env_name: str,
        python_version: str | None = None,
        *,
        packages: list[str] | None = None,
        created_by: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict:
        """创建虚拟环境；created_by 仅展示，owner_user_id 是授权主键。"""
        _validate_env_name(env_name)
        normalized_creator, normalized_owner = validate_runtime_creator(created_by, owner_user_id)
        packages_to_install = list(packages or [])
        self._validate_packages(packages_to_install)
        async with self._env_operation(f"env:{env_name}"):
            venv_path = self._get_venv_path(env_name)

            if os.path.exists(venv_path):
                # 调用方显式指定了一个已被占用的名字，不是本机故障：带码抛出，
                # 让控制面能判成 409 而不是把它混进服务端 500 与 ERROR 告警。
                raise RuntimeEnvAlreadyExistsError(f"虚拟环境 {env_name} 已存在")

            if not python_version:
                raise RuntimeError("python_version 不能为空")

            python_arg = _normalize_python_version(python_version)
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
                "created_by": normalized_creator,
                "owner_user_id": normalized_owner,
                "packages_count": 0,
            }
            manifest_path = os.path.join(venv_path, "manifest.json")
            write_runtime_manifest(manifest_path, manifest)

            if packages_to_install:
                await self._install_packages_locked(env_name, packages_to_install, upgrade=False)

            await self._update_packages_count(env_name)

            manifest = load_runtime_manifest(manifest_path, required=True)
            key, description = runtime_manifest_metadata(manifest)
            persisted_creator, persisted_owner = runtime_manifest_creator(manifest)

            logger.info(f"虚拟环境创建成功: {env_name} (Python {actual_version})")

            return {
                "name": env_name,
                "path": venv_path,
                "python_version": actual_version,
                "python_executable": python_exe,
                "created_at": manifest["created_at"],
                "created_by": persisted_creator,
                "owner_user_id": persisted_owner,
                "packages_count": manifest.get("packages_count", 0),
                "key": key,
                "description": description,
                "scope": "shared" if env_name.startswith("shared-") else "private",
            }

    async def delete_env(self, env_name: str) -> bool:
        """删除虚拟环境"""
        _validate_env_name(env_name)
        async with self._env_operation(f"env:{env_name}"):
            venv_path = self._get_venv_path(env_name)

            if not os.path.exists(venv_path):
                return False

            # rmtree 前二次校验：解析后的路径必须仍是 venvs_dir 的直接子目录
            if self.venvs_dir is None:
                raise RuntimeError("venvs_dir 未设置")
            base = Path(self.venvs_dir).resolve()
            target = Path(venv_path).resolve()
            if target.parent != base:
                raise ValueError(f"拒绝删除越界路径: {venv_path}")

            try:
                shutil.rmtree(str(target))
                logger.info(f"虚拟环境删除成功: {env_name}")
                return True
            except Exception as e:
                logger.error(f"删除虚拟环境失败: {e}")
                raise RuntimeError(f"删除虚拟环境失败: {e}")

    async def install_packages(self, env_name: str, packages: list[str], upgrade: bool = False) -> dict:
        """安装包到虚拟环境"""
        _validate_env_name(env_name)
        self._validate_packages(packages)
        async with self._env_operation(f"env:{env_name}"):
            return await self._install_packages_locked(env_name, packages, upgrade)

    async def _install_packages_locked(
        self,
        env_name: str,
        packages: list[str],
        upgrade: bool,
    ) -> dict:
        venv_path = self._get_venv_path(env_name)
        if not os.path.exists(venv_path):
            raise RuntimeError(f"虚拟环境 {env_name} 不存在")
        python_exe = self._get_python_executable(venv_path)
        # P0-01：强制 wheel-only + 禁用可执行的构建后端。
        #  --only-binary=:all: 让 uv 只接受 wheel，绝不从 sdist 构建
        #    → 阻断 setup.py / PEP 517 build backend 的任意代码执行；
        #  --no-build-isolation 保留（我们本来就不构建）；
        #  --index-strategy first-index 只从第一个匹配的 index 取（避免污染）；
        #  额外传 `--` 隔离，防止有人把 `-r requirements.txt` 藏进包名列表。
        args = [
            "uv",
            "pip",
            "install",
            "--python",
            python_exe,
            "--only-binary",
            ":all:",
            "--index-strategy",
            "first-index",
        ]
        if upgrade:
            args.append("-U")
        args.append("--")
        args.extend(packages)
        res = await run_command(args, timeout=1800)
        if res.exit_code != 0:
            raise RuntimeError(f"安装包失败: {res.stderr}")
        await self._update_packages_count(env_name)
        logger.info(f"安装包成功: {packages} -> {env_name}")
        return {"success": True, "installed": packages, "output": res.stdout}

    async def uninstall_packages(self, env_name: str, packages: list[str]) -> dict:
        """从虚拟环境卸载包"""
        _validate_env_name(env_name)
        self._validate_packages(packages)
        async with self._env_operation(f"env:{env_name}"):
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
        _validate_env_name(env_name)
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
            manifest = load_runtime_manifest(manifest_path)
            runtime_manifest_metadata(manifest)
            runtime_manifest_creator(manifest)
            manifest["packages_count"] = len(packages)
            write_runtime_manifest(manifest_path, manifest)
        except Exception as e:
            logger.warning(f"更新包数量失败: {e}")

    def _validate_packages(self, packages: list[str]) -> None:
        """
        校验包名格式，防止注入与 RCE。

        P0-01：
        - 严格 PEP 508 白名单 PACKAGE_PATTERN。
        - 命中任一 _FORBIDDEN_PACKAGE_SUBSTRINGS 直接拒。
        - 禁止 `-` 开头（uv 参数注入）、`=` `+` `~` 等运算符打头。
        - 单条最大长度 256 防超长构造。
        """

        def _is_bad(package: str) -> bool:
            if not package or not isinstance(package, str):
                return True
            if len(package) > 256:
                return True
            if package[0] in "-=+~<>!":
                return True
            if not PACKAGE_PATTERN.match(package):
                return True
            lower = package.lower()
            for token in _FORBIDDEN_PACKAGE_SUBSTRINGS:
                if token in lower:
                    return True
            return False

        invalid = [p for p in packages if _is_bad(p)]
        if invalid:
            raise RuntimeError(f"非法包名（拒绝 URL/VCS/direct-reference/本地路径/shell 元字符）: {invalid}")

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


# 公开的名称白名单校验入口，供 API 层复用
validate_env_name = _validate_env_name
