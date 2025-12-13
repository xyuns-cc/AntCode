"""安全任务执行器 - 资源限制与代码安全扫描

支持跨平台运行 (Linux/macOS/Windows)
- Unix: 使用 resource 模块进行资源限制
- Windows: 使用 psutil 进行资源监控

安全特性:
- Zip 炸弹检测: 防止解压攻击
- 危险代码扫描: AST 解析检测危险函数调用 (os.system, subprocess 等)
- 资源限制: CPU 时间、内存、磁盘写入、进程数、文件描述符
- 实时监控: psutil 监控资源使用，超限自动终止

注意: 本执行器提供安全增强，但不是完全隔离的沙箱环境:
- 任务在项目目录中执行，可访问项目文件
- 无文件系统隔离 (任务可读写项目目录外的文件)
- 无网络隔离 (任务可进行网络请求)
- 无进程命名空间隔离

如需完全隔离，请考虑使用 Docker 容器执行任务。
"""
import asyncio
import contextlib
import os
import platform
import tempfile
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

from loguru import logger

# Unix-only 资源限制模块
try:
    import resource
    HAS_RESOURCE = True
except ImportError:
    resource = None
    HAS_RESOURCE = False

# 跨平台进程监控
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

IS_WINDOWS = platform.system() == "Windows"

from ..utils.exceptions import SecurityError

from .base import BaseExecutor, ExecutionContext, ExecutionResult, ExecutionStatus


@dataclass
class ResourceLimits:
    """资源限制配置"""
    cpu_time: int = 3600           # CPU 时间限制 (秒)
    wall_time: int = 7200          # 墙钟时间限制 (秒)
    memory_mb: int = 512           # 内存限制 (MB)
    disk_mb: int = 1024            # 磁盘写入限制 (MB)
    file_size_mb: int = 100        # 单文件大小限制 (MB)
    max_processes: int = 50        # 最大进程数
    max_open_files: int = 1024     # 最大打开文件数
    max_output_lines: int = 100000 # 最大输出行数


class SecureTaskExecutor(BaseExecutor):
    """安全任务执行器 - 资源限制与代码安全扫描
    
    安全特性:
    - Zip 炸弹检测: 检测压缩比异常的文件，防止解压攻击
    - 危险代码扫描: AST 解析检测 os.system、subprocess 等危险调用
    - 资源限制: CPU 时间、内存、磁盘写入、进程数、文件描述符
    - 实时监控: psutil 监控资源使用，超限自动终止进程
    
    限制说明:
    - 任务在项目目录执行，可访问项目文件
    - 无文件系统/网络/进程隔离
    - 临时目录仅用于 TEMP/HOME 环境变量
    """

    # 默认资源限制
    DEFAULT_LIMITS = ResourceLimits()

    # Zip 炸弹检测配置
    ZIP_MAX_RATIO = 100          # 最大压缩比
    ZIP_MAX_FILES = 10000        # 最大文件数
    ZIP_MAX_TOTAL_SIZE = 2048    # 最大解压大小 (MB)

    # 危险命令黑名单
    BLOCKED_PATTERNS: Set[str] = {
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=/dev/zero",
        ":(){ :|:& };:",    # Fork 炸弹
        "shutdown",
        "reboot",
        "halt",
        "init 0",
        "init 6",
        "/dev/sda",
        "/dev/hda",
    }

    # 敏感模块 (仅警告不阻止)
    SENSITIVE_IMPORTS: Set[str] = {
        "ctypes",
        "subprocess", 
        "multiprocessing",
        "os.system",
        "os.popen",
        "commands",
    }

    def __init__(
        self, 
        signals=None, 
        max_concurrent=5, 
        default_timeout=3600,
        enable_security_scan=True,
        sandbox_base_dir: str = None
    ):
        super().__init__(signals, max_concurrent, default_timeout)
        self.enable_security_scan = enable_security_scan
        self.sandbox_base_dir = sandbox_base_dir or tempfile.gettempdir()
        self._sandbox_dirs: dict[str, Path] = {}

    async def _do_execute(self, context: ExecutionContext) -> ExecutionResult:
        """执行任务 (带安全检查和资源限制)"""
        result = ExecutionResult(
            execution_id=context.execution_id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now().isoformat()
        )

        sandbox_dir = None
        try:
            # 1. 安全扫描
            if self.enable_security_scan:
                await self._security_scan(context)

            # 2. 创建临时目录 (用于 TEMP/HOME 环境变量，不影响执行目录)
            sandbox_dir = await self._create_sandbox_dir(context)

            # 3. 执行目录始终使用项目目录，沙箱仅用于临时输出
            work_dir = context.work_dir

            # 4. 构建资源限制
            limits = self._build_limits(context)

            # 5. 执行任务 (传入沙箱目录用于临时文件)
            result = await self._execute_with_limits(
                context, work_dir, limits, result, sandbox_dir
            )

        except SecurityError as e:
            logger.error(f"安全检查失败: {e}")
            result.status = ExecutionStatus.FAILED
            result.error_message = f"[安全检查] {str(e)}"
            result.finished_at = datetime.now().isoformat()
        except asyncio.TimeoutError:
            result.status = ExecutionStatus.TIMEOUT
            result.error_message = f"执行超时"
            result.finished_at = datetime.now().isoformat()
        except Exception as e:
            logger.error(f"执行异常: {e}")
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)
            result.finished_at = datetime.now().isoformat()
        finally:
            # 清理临时目录
            if sandbox_dir and sandbox_dir.exists():
                await self._cleanup_sandbox(sandbox_dir)
                self._sandbox_dirs.pop(context.execution_id, None)

        return result

    async def _security_scan(self, context: ExecutionContext):
        """安全扫描 - 检测恶意内容"""
        work_dir = Path(context.work_dir)
        if not work_dir.exists():
            return

        # 1. 检查压缩文件 (防止 Zip 炸弹)
        for archive in work_dir.rglob("*.zip"):
            await self._check_zip_bomb(archive)

        # 2. 扫描代码中的危险命令
        for py_file in work_dir.rglob("*.py"):
            await self._scan_dangerous_code(py_file)

        # 3. 扫描 shell 脚本
        for sh_file in work_dir.rglob("*.sh"):
            await self._scan_dangerous_code(sh_file)

    async def _check_zip_bomb(self, archive_path: Path):
        """检测 Zip 炸弹"""
        try:
            compressed_size = archive_path.stat().st_size
            if compressed_size == 0:
                raise SecurityError(f"空的压缩文件: {archive_path.name}")

            with zipfile.ZipFile(archive_path, 'r') as zf:
                # 统计解压后大小和文件数
                total_size = 0
                file_count = 0

                for info in zf.infolist():
                    file_count += 1
                    total_size += info.file_size

                    # 检查文件数
                    if file_count > self.ZIP_MAX_FILES:
                        raise SecurityError(
                            f"压缩文件包含过多文件: {file_count} > {self.ZIP_MAX_FILES}"
                        )

                # 压缩比检查
                ratio = total_size / compressed_size if compressed_size > 0 else 0
                if ratio > self.ZIP_MAX_RATIO:
                    raise SecurityError(
                        f"可疑压缩文件 {archive_path.name}: 压缩比 {ratio:.1f} 超过阈值 {self.ZIP_MAX_RATIO}"
                    )

                # 解压大小检查
                max_size_bytes = self.ZIP_MAX_TOTAL_SIZE * 1024 * 1024
                if total_size > max_size_bytes:
                    raise SecurityError(
                        f"压缩文件 {archive_path.name}: 解压大小 {total_size/1024/1024:.1f}MB 超过限制 {self.ZIP_MAX_TOTAL_SIZE}MB"
                    )

        except zipfile.BadZipFile:
            raise SecurityError(f"无效的压缩文件: {archive_path.name}")
        except SecurityError:
            raise
        except Exception as e:
            logger.warning(f"Zip 检查异常: {e}")

    async def _scan_dangerous_code(self, file_path: Path):
        """扫描危险代码
        
        对 Python 文件使用 AST 解析，避免误报注释中的内容
        对其他文件使用文本匹配
        """
        try:
            if file_path.suffix == '.py':
                await self._scan_python_ast(file_path)
            else:
                await self._scan_text_patterns(file_path)
        except SecurityError:
            raise
        except Exception as e:
            logger.debug(f"代码扫描异常: {e}")

    async def _scan_python_ast(self, file_path: Path):
        """使用 AST 解析 Python 代码，避免误报注释"""
        import ast

        try:
            content = file_path.read_text(errors='ignore')
            tree = ast.parse(content)
        except SyntaxError:
            # 语法错误的文件回退到文本匹配
            await self._scan_text_patterns(file_path)
            return

        # 危险函数调用检测
        dangerous_calls = {
            ('os', 'system'),
            ('os', 'popen'),
            ('os', 'execl'),
            ('os', 'execle'),
            ('os', 'execlp'),
            ('os', 'execv'),
            ('os', 'execve'),
            ('os', 'execvp'),
            ('os', 'spawnl'),
            ('os', 'spawnle'),
            ('subprocess', 'call'),
            ('subprocess', 'run'),
            ('subprocess', 'Popen'),
            ('commands', 'getoutput'),
            ('commands', 'getstatusoutput'),
        }

        # 收集导入别名
        import_aliases = {}  # alias -> module

        for node in ast.walk(tree):
            # 收集 import 语句的别名
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    import_aliases[name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    name = alias.asname or alias.name
                    import_aliases[name] = f"{module}.{alias.name}"

            # 检测函数调用
            elif isinstance(node, ast.Call):
                # 检测危险函数调用 (os.system, subprocess.run 等)
                func_info = self._get_call_info(node, import_aliases)
                if func_info in dangerous_calls:
                    logger.warning(
                        f"任务调用了危险函数: {func_info[0]}.{func_info[1]} "
                        f"(文件: {file_path.name}, 行: {node.lineno})"
                    )

                # 检测 eval/exec/compile 调用
                if isinstance(node.func, ast.Name):
                    if node.func.id in ('eval', 'exec', 'compile'):
                        logger.warning(
                            f"任务使用了 {node.func.id}() "
                            f"(文件: {file_path.name}, 行: {node.lineno})"
                        )

        # 仍然检查字符串字面量中的危险命令
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for blocked in self.BLOCKED_PATTERNS:
                    if blocked in node.value:
                        raise SecurityError(
                            f"检测到危险命令 '{blocked[:30]}...' "
                            f"在文件 {file_path.name} 行 {node.lineno}"
                        )

    def _get_call_info(self, node: 'ast.Call', aliases: dict) -> tuple:
        """获取函数调用信息 (module, function)"""
        import ast

        if isinstance(node.func, ast.Attribute):
            # os.system() 形式
            if isinstance(node.func.value, ast.Name):
                module = aliases.get(node.func.value.id, node.func.value.id)
                return (module, node.func.attr)
        elif isinstance(node.func, ast.Name):
            # 直接调用 (可能是 from os import system)
            full_name = aliases.get(node.func.id, '')
            if '.' in full_name:
                parts = full_name.rsplit('.', 1)
                return (parts[0], parts[1])
        return ('', '')

    async def _scan_text_patterns(self, file_path: Path):
        """文本模式扫描（用于非 Python 文件）"""
        try:
            content = file_path.read_text(errors='ignore')

            # 检查危险命令
            for blocked in self.BLOCKED_PATTERNS:
                if blocked in content:
                    raise SecurityError(
                        f"检测到危险命令 '{blocked[:30]}...' 在文件 {file_path.name}"
                    )

            # 检查敏感模块导入 (仅警告)
            for imp in self.SENSITIVE_IMPORTS:
                if f"import {imp}" in content or f"from {imp}" in content:
                    logger.warning(f"任务使用了敏感模块: {imp} (文件: {file_path.name})")

        except SecurityError:
            raise
        except Exception as e:
            logger.debug(f"文本扫描异常: {e}")

    async def _create_sandbox_dir(self, context: ExecutionContext) -> Optional[Path]:
        """创建临时工作目录
        
        用于存放临时文件，作为 TEMP/HOME 环境变量的值。
        注意: 这不是真正的沙箱隔离，任务仍在项目目录执行。
        """
        try:
            sandbox_base = Path(self.sandbox_base_dir) / "antcode_sandbox"
            sandbox_base.mkdir(exist_ok=True)

            task_dir = sandbox_base / f"task_{context.execution_id}"
            task_dir.mkdir(exist_ok=True)

            # Unix 系统设置权限
            if not IS_WINDOWS:
                try:
                    sandbox_base.chmod(0o755)
                    task_dir.chmod(0o755)
                except OSError:
                    pass

            self._sandbox_dirs[context.execution_id] = task_dir
            logger.debug(f"创建沙箱目录: {task_dir}")

            return task_dir
        except Exception as e:
            logger.warning(f"创建沙箱目录失败: {e}")
            return None

    async def _cleanup_sandbox(self, sandbox_dir: Path):
        """清理临时目录"""
        try:
            if sandbox_dir.exists():
                shutil.rmtree(sandbox_dir, ignore_errors=True)
                logger.debug(f"清理沙箱目录: {sandbox_dir}")
        except Exception as e:
            logger.warning(f"清理沙箱目录失败: {e}")

    def _build_limits(self, context: ExecutionContext) -> ResourceLimits:
        """构建资源限制配置"""
        limits = ResourceLimits()

        # 从 context 覆盖默认值
        if context.cpu_limit:
            limits.cpu_time = context.cpu_limit
        if context.memory_limit:
            limits.memory_mb = context.memory_limit
        if context.timeout:
            limits.wall_time = context.timeout

        return limits

    async def _execute_with_limits(
        self, 
        context: ExecutionContext, 
        work_dir: str,
        limits: ResourceLimits,
        result: ExecutionResult,
        sandbox_dir: Optional[Path] = None
    ) -> ExecutionResult:
        """带资源限制的执行
        
        Args:
            context: 执行上下文
            work_dir: 工作目录 (项目目录)
            limits: 资源限制配置
            result: 执行结果对象
            sandbox_dir: 沙箱目录 (用于临时文件输出)
        """
        # 构建命令和环境
        cmd = [context.python_executable, context.entry_point]
        cmd.extend(self._build_args(context.params))

        env = self._build_restricted_env(context, work_dir, sandbox_dir)

        # 创建子进程 (Windows 不支持 preexec_fn)
        subprocess_kwargs = {
            "cwd": work_dir,
            "env": env,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }

        # Unix 系统使用 preexec_fn 设置资源限制
        if not IS_WINDOWS and HAS_RESOURCE:
            subprocess_kwargs["preexec_fn"] = lambda: self._apply_resource_limits(limits)

        process = await asyncio.create_subprocess_exec(*cmd, **subprocess_kwargs)

        async with self._lock:
            self._running_tasks[context.execution_id] = process

        # 启动资源监控 (Windows 主要依赖此监控实现资源限制)
        monitor_task = None
        if HAS_PSUTIL:
            monitor_task = asyncio.create_task(
                self._monitor_process(process, limits, context.execution_id)
            )

        try:
            exit_code = await self._stream_output(
                process, 
                context.execution_id, 
                result, 
                limits.wall_time,
                limits.max_output_lines
            )

            result.exit_code = exit_code
            result.finished_at = datetime.now().isoformat()

            if exit_code == 0:
                result.status = ExecutionStatus.COMPLETED
            elif exit_code == 124:
                result.status = ExecutionStatus.TIMEOUT
                result.error_message = f"执行超时 ({limits.wall_time}s)"
            elif exit_code in (-15, -9):
                result.status = ExecutionStatus.CANCELLED
                result.error_message = "任务被取消或资源超限"
            else:
                result.status = ExecutionStatus.FAILED
                result.error_message = f"退出码: {exit_code}"

        finally:
            if monitor_task:
                monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor_task

            async with self._lock:
                self._running_tasks.pop(context.execution_id, None)

        return result

    def _apply_resource_limits(self, limits: ResourceLimits):
        """应用资源限制 (在子进程中执行)
        
        仅在 Unix 系统上生效，Windows 通过 psutil 监控实现
        """
        if IS_WINDOWS or not HAS_RESOURCE:
            # Windows 不支持 resource 模块，资源限制通过 _monitor_process 实现
            return

        try:
            # CPU 时间限制
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (limits.cpu_time, limits.cpu_time)
            )

            # 内存限制 (虚拟内存)
            memory_bytes = limits.memory_mb * 1024 * 1024
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_bytes, memory_bytes)
            )

            # 单文件大小限制
            file_size_bytes = limits.file_size_mb * 1024 * 1024
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (file_size_bytes, file_size_bytes)
            )

            # 进程数限制 (防止 Fork 炸弹) - 某些系统可能不支持
            try:
                resource.setrlimit(
                    resource.RLIMIT_NPROC,
                    (limits.max_processes, limits.max_processes)
                )
            except (ValueError, OSError):
                pass  # macOS 等系统可能限制此操作

            # 文件描述符限制
            resource.setrlimit(
                resource.RLIMIT_NOFILE,
                (limits.max_open_files, limits.max_open_files)
            )

            # 禁用核心转储
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        except Exception as e:
            # 某些系统可能不支持部分限制
            logger.debug(f"设置资源限制异常 (可忽略): {e}")

    def _build_restricted_env(
        self, 
        context: ExecutionContext, 
        work_dir: str,
        sandbox_dir: Optional[Path] = None
    ) -> dict:
        """构建受限环境变量 (跨平台兼容)
        
        Args:
            context: 执行上下文
            work_dir: 工作目录
            sandbox_dir: 沙箱目录 (用于临时文件)
        """
        # 路径分隔符: Windows 用分号，Unix 用冒号
        path_sep = ";" if IS_WINDOWS else ":"

        # 临时目录: 优先使用沙箱目录
        temp_dir = str(sandbox_dir) if sandbox_dir else tempfile.gettempdir()

        if IS_WINDOWS:
            # Windows 环境变量
            env = {
                "PATH": os.environ.get("PATH", ""),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
                "TEMP": temp_dir,
                "TMP": temp_dir,
                "USERPROFILE": temp_dir,
                "PYTHONPATH": f"{context.work_dir}{path_sep}{work_dir}",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        else:
            # Unix 环境变量
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": temp_dir,
                "TMPDIR": temp_dir,
                "PYTHONPATH": f"{context.work_dir}{path_sep}{work_dir}",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }

        # 添加用户环境变量 (过滤敏感项)
        sensitive_prefixes = (
            "AWS_", "AZURE_", "GCP_", "SECRET", "PASSWORD", 
            "TOKEN", "KEY", "CREDENTIAL", "PRIVATE"
        )

        for key, value in (context.environment or {}).items():
            key_upper = key.upper()
            if not any(key_upper.startswith(p) or p in key_upper for p in sensitive_prefixes):
                env[key] = value

        return env

    async def _monitor_process(
        self, 
        process, 
        limits: ResourceLimits, 
        execution_id: str
    ):
        """实时监控进程资源使用
        
        在 Windows 上这是主要的资源限制机制
        """
        if not HAS_PSUTIL:
            return

        try:
            ps_process = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            return

        memory_limit_bytes = limits.memory_mb * 1024 * 1024
        disk_limit_bytes = limits.disk_mb * 1024 * 1024
        check_interval = 1.0

        try:
            while process.returncode is None:
                try:
                    # 检查内存使用
                    mem_info = ps_process.memory_info()
                    if mem_info.rss > memory_limit_bytes:
                        logger.warning(
                            f"任务 {execution_id} 内存超限: "
                            f"{mem_info.rss/1024/1024:.1f}MB > {limits.memory_mb}MB"
                        )
                        self._force_kill_process(process, ps_process)
                        return

                    # 检查磁盘写入
                    try:
                        io_counters = ps_process.io_counters()
                        if io_counters.write_bytes > disk_limit_bytes:
                            logger.warning(
                                f"任务 {execution_id} 磁盘写入超限: "
                                f"{io_counters.write_bytes/1024/1024:.1f}MB > {limits.disk_mb}MB"
                            )
                            self._force_kill_process(process, ps_process)
                            return
                    except (psutil.AccessDenied, AttributeError):
                        pass  # 某些系统不支持 io_counters

                    await asyncio.sleep(check_interval)

                except psutil.NoSuchProcess:
                    return
                except psutil.AccessDenied:
                    await asyncio.sleep(check_interval)

        except asyncio.CancelledError:
            pass

    def _force_kill_process(self, process, ps_process=None):
        """强制终止进程及其子进程"""
        try:
            if HAS_PSUTIL and ps_process:
                # 先终止子进程
                try:
                    children = ps_process.children(recursive=True)
                    for child in children:
                        with contextlib.suppress(psutil.NoSuchProcess):
                            child.kill()
                except psutil.NoSuchProcess:
                    pass

            # 终止主进程
            try:
                process.kill()
            except ProcessLookupError:
                pass

        except Exception as e:
            logger.debug(f"强制终止进程异常: {e}")

    async def _stream_output(
        self, 
        process, 
        execution_id: str, 
        result: ExecutionResult, 
        timeout: int,
        max_lines: int = 100000
    ) -> int:
        """流式读取输出 (委托给基类)"""
        return await self._stream_output_base(process, execution_id, result, timeout, max_lines)

    async def cancel(self, execution_id: str) -> bool:
        """取消任务执行 (委托给基类)"""
        return await self._cancel_process(execution_id)
