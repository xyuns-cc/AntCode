from __future__ import annotations

import asyncio
import os

from loguru import logger

from src.core.command_runner import run_command
from src.models import Interpreter
from src.models.enums import InterpreterSource


_install_locks: dict[str, asyncio.Lock] = {}


def _lock_for(version: str) -> asyncio.Lock:
    key = f"python@{version}"
    if key not in _install_locks:
        _install_locks[key] = asyncio.Lock()
    return _install_locks[key]


class PythonEnvService:
    """Python 解释器管理（基于 mise）。"""

    async def list_remote_versions(self) -> list[str]:
        """列出可安装的远端 Python 版本。"""
        try:
            res = await run_command(["mise", "ls-remote", "python"], timeout=120)
        except FileNotFoundError:
            logger.warning("未检测到 mise，可安装版本列表返回为空")
            return []
        except Exception as e:
            # 处理 OSError: [Errno 2] No such file or directory: 'mise'
            if "No such file or directory" in str(e) and "mise" in str(e):
                logger.warning("未检测到 mise，可安装版本列表返回为空")
                return []
            raise
        if res.exit_code != 0:
            # mise 存在但调用失败，返回空列表而不是报错
            logger.warning(f"mise 列表命令失败: {res.stderr.strip()}")
            return []
        versions = []
        for line in res.stdout.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # 过滤非标准行，保留形如 3.x.y
            parts = s.split()
            ver = parts[0]
            versions.append(ver)
        return versions

    async def ensure_installed(
        self, version: str, created_by: int | None = None
    ) -> dict[str, str]:
        """确保指定版本已安装并返回安装信息。"""
        lock = _lock_for(version)
        async with lock:
            # 安装（幂等）
            res = await run_command(["mise", "install", "-y", f"python@{version}"], timeout=1800)
            if res.exit_code != 0:
                # mise 安装失败时也可能是已存在; 继续 where 核验
                logger.warning(f"mise install 返回非零: {res.exit_code} -> {res.stderr.strip()}")

            where = await run_command(["mise", "where", f"python@{version}"], timeout=60)
            if where.exit_code != 0:
                raise RuntimeError(f"查找解释器失败: {where.stderr.strip()}")
            install_dir = where.stdout.strip()
            # 推断可执行路径
            candidates = [
                os.path.join(install_dir, "bin", "python"),
                os.path.join(install_dir, "bin", "python3"),
            ]
            python_bin = next((p for p in candidates if os.path.exists(p)), candidates[0])
            info = {"version": version, "install_dir": install_dir, "python_bin": python_bin}

            # upsert interpreter record (mise)
            try:
                obj = await Interpreter.get_or_none(tool="python", version=version, source=InterpreterSource.MISE)
                if obj:
                    obj.install_dir = install_dir
                    obj.python_bin = python_bin
                    await obj.save()
                else:
                    await Interpreter.create(
                        tool="python",
                        version=version,
                        install_dir=install_dir,
                        python_bin=python_bin,
                        status="installed",
                        source=InterpreterSource.MISE,
                        created_by=created_by,
                    )
            except Exception as e:
                logger.warning(f"记录解释器到数据库失败: {e}")
            return info

    async def register_local(
        self, python_bin: str, created_by: int | None = None
    ) -> dict[str, str]:
        """注册本地解释器，读取版本并写入数据库。"""
        if not os.path.exists(python_bin):
            raise RuntimeError("python_bin 路径不存在")
        # 获取版本
        res = await run_command([python_bin, "--version"], timeout=30)
        if res.exit_code != 0:
            raise RuntimeError(f"读取版本失败: {res.stderr or res.stdout}")
        # 输出可能是 'Python 3.11.6' 取第二段
        ver = res.stdout.strip().split()[-1]
        install_dir = os.path.dirname(python_bin)
        try:
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
                    created_by=created_by,
                )
        except Exception as e:
            logger.warning(f"保存本地解释器失败: {e}")
        return {"version": ver, "install_dir": install_dir, "python_bin": python_bin}

    async def list_db(self, source: str | None = None) -> list[dict[str, str]]:
        from src.models import Interpreter as InterpreterModel
        query = InterpreterModel.filter(tool="python")
        if source:
            query = query.filter(source=source)
        rows = await query.all()
        return [
            {
                "id": r.public_id,
                "version": r.version,
                "install_dir": r.install_dir,
                "python_bin": r.python_bin,
                "source": r.source,
            }
            for r in rows
        ]

    async def uninstall(self, version: str) -> bool:
        """卸载指定版本（若正被 venv 使用，调用层应阻止）。"""
        lock = _lock_for(version)
        async with lock:
            res = await run_command(["mise", "uninstall", f"python@{version}"])
            if res.exit_code != 0:
                # 若未安装，视为成功卸载
                logger.warning(f"mise uninstall 返回非零: {res.exit_code} -> {res.stderr.strip()}")
            return True

    async def list_installed(self) -> list[dict[str, str]]:
        """列出已安装解释器（基于 mise 或本地扫描）。"""
        # 优先尝试 mise 的已安装列表
        try:
            res = await run_command(["mise", "ls", "--installed", "python"], timeout=60)
            if res.exit_code == 0 and res.stdout.strip():
                items = []
                for line in res.stdout.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    ver = s.split()[0]
                    where = await run_command(["mise", "where", f"python@{ver}"], timeout=30)
                    if where.exit_code != 0:
                        continue
                    install_dir = where.stdout.strip()
                    candidates = [
                        os.path.join(install_dir, "bin", "python"),
                        os.path.join(install_dir, "bin", "python3"),
                    ]
                    python_bin = next((p for p in candidates if os.path.exists(p)), candidates[0])
                    items.append({"version": ver, "install_dir": install_dir, "python_bin": python_bin})
                return items
        except Exception as e:
            logger.warning(f"列出已安装版本失败，回退扫描: {e}")

        # 回退：扫描 storage/mise 目录，尝试 where 校验常见版本
        try:
            storage_root = os.path.abspath(os.path.join(os.getcwd(), "storage"))
            mise_root = os.path.join(storage_root, "mise")
            # 无可靠索引时，直接返回空列表以避免误判
            if not os.path.exists(mise_root):
                return []
        except Exception:
            return []
        return []


python_env_service = PythonEnvService()
