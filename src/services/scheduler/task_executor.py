# src/services/task_executor.py (确保包含规则项目执行逻辑)
"""任务执行器"""
import asyncio
import os
import shutil
import sys
import tarfile
import zipfile
from datetime import datetime

from loguru import logger

from src.core.config import settings
from src.models.enums import ProjectType
from src.services.files.file_storage import file_storage_service
from src.services.logs.task_log_service import task_log_service
from src.services.projects.relation_service import relation_service
from src.services.envs.venv_service import project_venv_service
from src.models import Project
from src.services.scheduler.redis_task_service import redis_task_service
from src.utils.memory_optimizer import memory_optimized, StreamingBuffer


class TaskExecutor:
    """任务执行器"""

    def __init__(self):
        self.running_processes = {}
        self.work_dir = settings.TASK_EXECUTION_WORK_DIR

    @memory_optimized(max_memory_mb=200)  # 限制最大内存使用200MB
    async def execute(
            self,
            project,
            execution_id,
            params=None,
            environment_vars=None,
            timeout=3600
    ):
        """执行任务

        Args:
            project: 项目对象
            execution_id: 执行ID
            params: 执行参数
            environment_vars: 环境变量
            timeout: 超时时间（秒）

        Returns:
            执行结果
        """
        try:
            # 根据项目类型执行不同的逻辑
            if project.type == ProjectType.FILE:
                return await self._execute_file_project(
                    project, execution_id, params, environment_vars, timeout
                )
            elif project.type == ProjectType.CODE:
                return await self._execute_code_project(
                    project, execution_id, params, environment_vars, timeout
                )
            elif project.type == ProjectType.RULE:
                return await self._execute_rule_project(
                    project, execution_id, params, environment_vars, timeout
                )
            else:
                raise ValueError(f"不支持的项目类型: {project.type}")

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "任务执行超时",
                "timeout": timeout
            }
        except Exception as e:
            logger.error(f"执行任务失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _execute_rule_project(
            self,
            project,
            execution_id,
            params = None,
            environment_vars = None,
            timeout = 3600
    ):
        """执行规则项目 - 提交任务到Redis"""
        try:
            # 获取规则详情
            rule_detail = await project.rule_detail
            if not rule_detail:
                return {
                    "success": False,
                    "error": "规则项目详情不存在"
                }

            # 连接Redis
            await redis_task_service.connect()

            # 提交任务
            result = await redis_task_service.submit_rule_task(
                project=project,
                rule_detail=rule_detail,
                execution_id=execution_id,
                params=params
            )

            # 断开Redis连接
            await redis_task_service.disconnect()

            return result

        except Exception as e:
            logger.error(f"执行规则项目失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _execute_file_project(
            self,
            project,
            execution_id,
            params = None,
            environment_vars = None,
            timeout = 3600
    ):
        """执行文件项目"""
        try:
            # 获取文件项目详情
            file_detail = await relation_service.get_project_file_detail(project.id)
            if not file_detail:
                return {
                    "success": False,
                    "error": "文件项目详情不存在"
                }

            # 创建执行工作目录
            work_dir = await self._create_execution_workspace(execution_id)

            # 准备项目文件
            project_dir = await self._prepare_project_files(file_detail, work_dir)

            # 执行项目
            result = await self._run_python_project(
                project_dir=project_dir,
                file_detail=file_detail,
                execution_id=execution_id,
                params=params,
                environment_vars=environment_vars,
                timeout=timeout
            )

            return result

        except Exception as e:
            logger.error(f"执行文件项目失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            # 根据配置决定是否清理工作目录
            if settings.CLEANUP_WORKSPACE_ON_COMPLETION:
                await self._cleanup_workspace(execution_id)

    async def _execute_code_project(
            self,
            project,
            execution_id,
            params = None,
            environment_vars = None,
            timeout = 3600
    ):
        """执行代码项目"""
        try:
            # 获取代码项目详情
            code_detail = await relation_service.get_project_code_detail(project.id)
            if not code_detail:
                return {
                    "success": False,
                    "error": "代码项目详情不存在"
                }

            # 创建执行工作目录
            work_dir = await self._create_execution_workspace(execution_id)

            # 创建代码文件
            code_file = await self._create_code_file(code_detail, work_dir)

            # 执行代码
            result = await self._run_python_code(
                code_file=code_file,
                code_detail=code_detail,
                execution_id=execution_id,
                params=params,
                environment_vars=environment_vars,
                timeout=timeout
            )

            return result

        except Exception as e:
            logger.error(f"执行代码项目失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            # 根据配置决定是否清理工作目录
            if settings.CLEANUP_WORKSPACE_ON_COMPLETION:
                await self._cleanup_workspace(execution_id)

    async def _create_execution_workspace(self, execution_id):
        """创建执行工作目录"""
        work_dir = os.path.join(self.work_dir, execution_id)
        os.makedirs(work_dir, exist_ok=True)
        logger.info(f"创建执行工作目录: {work_dir}")
        return work_dir

    async def _prepare_project_files(self, file_detail, work_dir):
        """准备项目文件"""
        # 获取文件路径
        file_path = file_storage_service.get_file_path(file_detail.file_path)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"项目文件不存在: {file_path}")

        if file_detail.is_compressed:
            # 压缩文件已在上传时解压，file_path 指向解压后的目录
            # 直接使用解压后的目录
            logger.info(f"使用已解压的项目目录: {file_path}")
            return file_path
        else:
            # 单个文件，复制到工作目录
            project_dir = os.path.join(work_dir, "project")
            os.makedirs(project_dir, exist_ok=True)

            # 确保使用正确的文件名
            filename = os.path.basename(file_detail.original_name)
            target_file = os.path.join(project_dir, filename)
            shutil.copy2(file_path, target_file)
            logger.info(f"复制文件: {file_path} -> {target_file}")

            return project_dir

    async def _extract_archive(self, archive_path, extract_dir, original_name):
        """解压压缩文件"""
        try:
            if original_name.endswith('.zip'):
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                    logger.info(f"解压ZIP文件: {archive_path} -> {extract_dir}")
            elif original_name.endswith('.tar.gz'):
                with tarfile.open(archive_path, 'r:gz') as tar_ref:
                    tar_ref.extractall(extract_dir)
                    logger.info(f"解压TAR.GZ文件: {archive_path} -> {extract_dir}")
            else:
                raise ValueError(f"不支持的压缩格式: {original_name}")
        except Exception as e:
            logger.error(f"解压文件失败: {e}")
            raise

    async def _create_code_file(self, code_detail, work_dir):
        """创建代码文件"""
        project_dir = os.path.join(work_dir, "project")
        os.makedirs(project_dir, exist_ok=True)

        # 确定文件名
        if code_detail.entry_point:
            filename = code_detail.entry_point
        else:
            filename = f"main.{code_detail.language}"

        # 使用绝对路径
        code_file = os.path.abspath(os.path.join(project_dir, filename))

        # 写入代码内容
        with open(code_file, 'w', encoding='utf-8') as f:
            f.write(code_detail.content)

        logger.info(f"创建代码文件: {code_file}")
        return code_file

    async def _run_python_project(
            self,
            project_dir,
            file_detail,
            execution_id,
            params = None,
            environment_vars = None,
            timeout = 3600
    ):
        """运行Python项目"""
        # 生成日志文件路径
        log_paths = task_log_service.generate_log_paths(execution_id, f"project_{file_detail.project_id}")
        log_file_path = log_paths["log_file_path"]
        error_log_path = log_paths["error_log_path"]

        try:
            # 确定入口文件
            if file_detail.entry_point:
                # 只使用文件名，不包含路径
                entry_filename = os.path.basename(file_detail.entry_point)
                entry_file = os.path.join(project_dir, entry_filename)
            else:
                # 查找main.py或其他Python文件
                python_files = [f for f in os.listdir(project_dir) if f.endswith('.py')]
                if 'main.py' in python_files:
                    entry_file = os.path.join(project_dir, 'main.py')
                elif python_files:
                    entry_file = os.path.join(project_dir, python_files[0])
                else:
                    return {
                        "success": False,
                        "error": "未找到Python入口文件"
                    }

            if not os.path.exists(entry_file):
                return {
                    "success": False,
                    "error": f"入口文件不存在: {entry_file}"
                }

            # 准备环境变量
            env = os.environ.copy()
            if environment_vars:
                env.update(environment_vars)

            # 添加项目目录到Python路径
            env['PYTHONPATH'] = project_dir + ':' + env.get('PYTHONPATH', '')

            # 选择解释器：优先使用项目绑定的虚拟环境
            selected_python = sys.executable
            try:
                proj = await Project.get(id=file_detail.project_id)
                if proj and proj.venv_path:
                    selected_python = project_venv_service.venv_python(proj.venv_path)
                    logger.info(f"使用项目绑定的虚拟环境解释器: {selected_python}")
                else:
                    logger.info("未绑定虚拟环境，使用默认解释器")
            except Exception as _e:
                logger.warning(f"获取项目虚拟环境失败，使用默认解释器: {_e}")

            # 构建命令 - 使用绝对路径
            if not os.path.isabs(entry_file):
                entry_file = os.path.abspath(entry_file)
            cmd = [selected_python, entry_file]

            # 如果有参数，添加到命令行
            if params:
                cmd.extend(self._build_command_args(params))

            logger.info(f"执行命令: {' '.join(cmd)}")
            logger.info(f"工作目录: {project_dir}")

            # 记录执行开始日志
            await task_log_service.write_log(
                log_file_path,
                f"🚀 开始执行项目: {file_detail.original_name}",
                execution_id=execution_id
            )
            await task_log_service.write_log(
                log_file_path,
                f"📁 工作目录: {project_dir}",
                execution_id=execution_id
            )
            await task_log_service.write_log(
                log_file_path,
                f"⚡ 执行命令: {' '.join(cmd)}",
                execution_id=execution_id
            )
            if params:
                await task_log_service.write_log(
                    log_file_path,
                    f"📝 执行参数: {params}",
                    execution_id=execution_id
                )

            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=project_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 实时读取输出并记录日志
            stdout_text, stderr_text, exit_code = await self._stream_process_output(
                process, log_file_path, error_log_path, execution_id, timeout
            )

            logger.info(f"执行完成，退出码: {exit_code}")

            # 记录执行结束
            status_msg = "🎉 执行成功" if exit_code == 0 else f"❌ 执行失败 (退出码: {exit_code})"
            await task_log_service.write_log(
                log_file_path,
                f"✅ 执行完成，退出码: {exit_code}",
                execution_id=execution_id
            )
            await task_log_service.write_log(
                log_file_path,
                status_msg,
                execution_id=execution_id
            )

            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "error": stderr_text if exit_code != 0 else None,
                "log_file_path": log_file_path,
                "error_log_path": error_log_path
            }

        except Exception as e:
            logger.error(f"运行Python项目失败: {e}")

            # 记录异常到日志文件
            try:
                await task_log_service.write_log(
                    error_log_path,
                    f"💥 执行异常: {str(e)}",
                    execution_id=execution_id
                )
            except:
                pass  # 避免日志记录失败影响主流程

            return {
                "success": False,
                "error": str(e),
                "log_file_path": log_file_path,
                "error_log_path": error_log_path
            }

    async def _run_python_code(
            self,
            code_file,
            code_detail,
            execution_id,
            params = None,
            environment_vars = None,
            timeout = 3600
    ):
        """运行Python代码"""
        # 生成日志文件路径
        log_paths = task_log_service.generate_log_paths(execution_id, f"code_{code_detail.project_id}")
        log_file_path = log_paths["log_file_path"]
        error_log_path = log_paths["error_log_path"]

        try:
            # 准备环境变量
            env = os.environ.copy()
            if environment_vars:
                env.update(environment_vars)

            # 添加代码文件目录到Python路径
            code_dir = os.path.dirname(code_file)
            env['PYTHONPATH'] = code_dir + ':' + env.get('PYTHONPATH', '')

            # 选择解释器：优先使用项目绑定的虚拟环境
            selected_python = sys.executable
            try:
                proj = await Project.get(id=code_detail.project_id)
                if proj and proj.venv_path:
                    selected_python = project_venv_service.venv_python(proj.venv_path)
                    logger.info(f"使用项目绑定的虚拟环境解释器: {selected_python}")
                else:
                    logger.info("未绑定虚拟环境，使用默认解释器")
            except Exception as _e:
                logger.warning(f"获取项目虚拟环境失败，使用默认解释器: {_e}")

            # 构建命令
            cmd = [selected_python, code_file]

            # 如果有参数，添加到命令行
            if params:
                cmd.extend(self._build_command_args(params))

            logger.info(f"执行命令: {' '.join(cmd)}")
            logger.info(f"工作目录: {code_dir}")

            # 记录执行开始日志
            await task_log_service.write_log(
                log_file_path,
                f"🚀 开始执行代码: {os.path.basename(code_file)}",
                execution_id=execution_id
            )
            await task_log_service.write_log(
                log_file_path,
                f"⚡ 执行命令: {' '.join(cmd)}",
                execution_id=execution_id
            )
            if params:
                await task_log_service.write_log(
                    log_file_path,
                    f"📝 执行参数: {params}",
                    execution_id=execution_id
                )

            # 执行命令
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=code_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # 实时读取输出并记录日志
            stdout_text, stderr_text, exit_code = await self._stream_process_output(
                process, log_file_path, error_log_path, execution_id, timeout
            )

            logger.info(f"执行完成，退出码: {exit_code}")

            # 记录执行结束
            status_msg = "🎉 执行成功" if exit_code == 0 else f"❌ 执行失败 (退出码: {exit_code})"
            await task_log_service.write_log(
                log_file_path,
                f"✅ 执行完成，退出码: {exit_code}",
                execution_id=execution_id
            )
            await task_log_service.write_log(
                log_file_path,
                status_msg,
                execution_id=execution_id
            )

            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "error": stderr_text if exit_code != 0 else None,
                "log_file_path": log_file_path,
                "error_log_path": error_log_path
            }

        except Exception as e:
            logger.error(f"运行Python代码失败: {e}")

            # 记录异常到日志文件
            try:
                await task_log_service.write_log(
                    error_log_path,
                    f"💥 执行异常: {str(e)}",
                    execution_id=execution_id
                )
            except:
                pass  # 避免日志记录失败影响主流程

            return {
                "success": False,
                "error": str(e),
                "log_file_path": log_file_path,
                "error_log_path": error_log_path
            }

    def _build_command_args(self, params):
        """
        构建命令行参数

        支持多种参数格式：
        1. 简单键值对: {"name": "value"} -> ["--name", "value"]
        2. 布尔值: {"debug": True} -> ["--debug"]
        3. 列表值: {"files": ["a.txt", "b.txt"]} -> ["--files", "a.txt", "b.txt"]
        4. 位置参数: {"_args": ["arg1", "arg2"]} -> ["arg1", "arg2"]
        """
        args = []

        # 处理位置参数
        if "_args" in params:
            pos_args = params["_args"]
            if isinstance(pos_args, list):
                args.extend([str(arg) for arg in pos_args])
            else:
                args.append(str(pos_args))

        # 处理命名参数
        for key, value in params.items():
            if key == "_args":
                continue

            if isinstance(value, bool):
                # 布尔值参数
                if value:
                    args.append(f"--{key}")
            elif isinstance(value, list):
                # 列表参数
                args.append(f"--{key}")
                args.extend([str(v) for v in value])
            elif value is not None:
                # 普通键值对参数
                args.extend([f"--{key}", str(value)])

        return args

    async def _stream_process_output(
            self,
            process,
            log_file_path,
            error_log_path,
            execution_id,
            timeout
    ):
        """
        实时流式读取进程输出并记录到日志（内存优化版本）

        Returns:
            (stdout_text, stderr_text, exit_code)
        """
        # 使用流式缓冲区管理内存
        stdout_buffer = StreamingBuffer(max_size=4 * 1024 * 1024)  # 4MB缓冲区
        stderr_buffer = StreamingBuffer(max_size=4 * 1024 * 1024)  # 4MB缓冲区
        
        # 设置溢出处理
        stdout_overflow_lines = []
        stderr_overflow_lines = []
        
        def stdout_overflow_handler(data):
            lines = data.decode('utf-8', errors='ignore').split('\n')
            stdout_overflow_lines.extend(lines)
            # 只保留最后1000行，避免内存无限增长
            if len(stdout_overflow_lines) > 1000:
                stdout_overflow_lines[:] = stdout_overflow_lines[-1000:]
        
        def stderr_overflow_handler(data):
            lines = data.decode('utf-8', errors='ignore').split('\n')
            stderr_overflow_lines.extend(lines)
            if len(stderr_overflow_lines) > 1000:
                stderr_overflow_lines[:] = stderr_overflow_lines[-1000:]
        
        stdout_buffer.set_overflow_callback(stdout_overflow_handler)
        stderr_buffer.set_overflow_callback(stderr_overflow_handler)

        async def read_stdout():
            """读取标准输出流（内存优化）"""
            try:
                while True:
                    chunk = await process.stdout.read(8192)  # 8KB chunks
                    if not chunk:
                        break

                    stdout_buffer.write(chunk)
                    
                    # 处理完整的行
                    while b'\n' in stdout_buffer.buffer:
                        line_data = stdout_buffer.read(stdout_buffer.buffer.find(b'\n') + 1)
                        line_text = line_data.decode('utf-8', errors='ignore').rstrip('\n\r')
                        
                        if line_text:
                            # 实时写入日志文件
                            await task_log_service.write_log(
                                log_file_path,
                                line_text,
                                execution_id=execution_id,
                                add_timestamp=False
                            )
                            
                            # 实时推送到WebSocket
                            await self._broadcast_log_line(execution_id, "stdout", line_text)
                            
            except Exception as e:
                logger.error(f"读取标准输出失败: {e}")

        async def read_stderr():
            """读取错误输出流（内存优化）"""
            try:
                while True:
                    chunk = await process.stderr.read(8192)  # 8KB chunks
                    if not chunk:
                        break

                    stderr_buffer.write(chunk)
                    
                    # 处理完整的行
                    while b'\n' in stderr_buffer.buffer:
                        line_data = stderr_buffer.read(stderr_buffer.buffer.find(b'\n') + 1)
                        line_text = line_data.decode('utf-8', errors='ignore').rstrip('\n\r')
                        
                        if line_text:
                            # 实时写入错误日志文件
                            await task_log_service.write_log(
                                error_log_path,
                                line_text,
                                execution_id=execution_id,
                                add_timestamp=False
                            )
                            
                            # 实时推送到WebSocket
                            await self._broadcast_log_line(execution_id, "stderr", line_text)
                            
            except Exception as e:
                logger.error(f"读取错误输出失败: {e}")

        try:
            # 并发读取stdout和stderr，同时等待进程完成
            await asyncio.wait_for(
                asyncio.gather(
                    read_stdout(),
                    read_stderr(),
                    process.wait()
                ),
                timeout=timeout
            )

            exit_code = process.returncode

        except asyncio.TimeoutError:
            logger.warning(f"进程执行超时 ({timeout}秒)，正在终止...")

            # 记录超时信息
            timeout_msg = f"⏰ 进程执行超时 ({timeout}秒)，正在终止进程"
            await task_log_service.write_log(
                error_log_path,
                timeout_msg,
                execution_id=execution_id
            )

            # 终止进程
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                # 强制杀死进程
                process.kill()
                await process.wait()

            exit_code = -1  # 超时退出码
            stderr_overflow_lines.append(timeout_msg)

        except Exception as e:
            logger.error(f"流式读取进程输出失败: {e}")

            # 记录异常信息
            error_msg = f"💥 读取进程输出异常: {str(e)}"
            await task_log_service.write_log(
                error_log_path,
                error_msg,
                execution_id=execution_id
            )

            # 确保进程被清理
            if process.returncode is None:
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass

            exit_code = process.returncode if process.returncode is not None else -2
            stderr_overflow_lines.append(error_msg)

        # 收集剩余的输出
        remaining_stdout = stdout_buffer.read().decode('utf-8', errors='ignore')
        remaining_stderr = stderr_buffer.read().decode('utf-8', errors='ignore')
        
        # 合并所有输出 (内存优化：只保留摘要)
        stdout_text = f"输出行数: {len(stdout_overflow_lines)}"
        if remaining_stdout:
            stdout_text += f"\n最后输出: {remaining_stdout[-500:]}"  # 只保留最后500字符
            
        stderr_text = f"错误行数: {len(stderr_overflow_lines)}" 
        if remaining_stderr:
            stderr_text += f"\n最后错误: {remaining_stderr[-500:]}"  # 只保留最后500字符
        
        # 清理缓冲区释放内存
        stdout_buffer.clear()
        stderr_buffer.clear()
        
        return stdout_text, stderr_text, exit_code

    async def _broadcast_log_line(self, execution_id, log_type, line_text):
        """
        实时广播日志行到WebSocket连接（已移除WebSocket功能）

        Args:
            execution_id: 执行ID
            log_type: 日志类型 (stdout/stderr)
            line_text: 日志行内容
        """
        # WebSocket功能已被移除，此方法保留以保持兼容性
        pass

    async def _cleanup_workspace(self, execution_id):
        """
        清理执行工作目录
        
        Args:
            execution_id: 执行ID
        """
        try:
            work_dir = os.path.join(self.work_dir, execution_id)
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
                logger.info(f"已清理执行工作目录: {work_dir}")
            else:
                logger.debug(f"工作目录不存在，无需清理: {work_dir}")
        except Exception as e:
            logger.error(f"清理工作目录失败 {work_dir}: {e}")

    async def cleanup_old_workspaces(self, max_age_hours = 24):
        """
        清理过期的工作目录
        
        Args:
            max_age_hours: 最大保留时间（小时），默认24小时
        """
        try:
            if not os.path.exists(self.work_dir):
                return
                
            current_time = datetime.now()
            cleaned_count = 0
            
            for dir_name in os.listdir(self.work_dir):
                dir_path = os.path.join(self.work_dir, dir_name)
                if not os.path.isdir(dir_path):
                    continue
                    
                # 检查目录创建时间
                dir_stat = os.stat(dir_path)
                dir_age_hours = (current_time.timestamp() - dir_stat.st_ctime) / 3600
                
                if dir_age_hours > max_age_hours:
                    try:
                        shutil.rmtree(dir_path)
                        cleaned_count += 1
                        logger.debug(f"清理过期目录: {dir_path} (创建于{dir_age_hours:.1f}小时前)")
                    except Exception as e:
                        logger.error(f"清理过期目录失败 {dir_path}: {e}")
                        
            if cleaned_count > 0:
                logger.info(f"清理了 {cleaned_count} 个过期的执行工作目录")
                
        except Exception as e:
            logger.error(f"批量清理工作目录失败: {e}")
