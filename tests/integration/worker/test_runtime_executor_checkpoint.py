"""
Checkpoint 9: Runtime + Executor 验证

验证：
- 多任务并发执行
- runtime cache 复用验证
- timeout/kill 行为验证

Requirements: 6.1, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3
"""

import asyncio
import os
import tempfile
import time
from datetime import datetime

import pytest
from loguru import logger

from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecPlan, RuntimeHandle
from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.runtime.hash import compute_runtime_hash
from antcode_worker.runtime.spec import LockSource, PythonSpec, RuntimeSpec


@pytest.fixture
def temp_venvs_dir():
    """创建临时虚拟环境目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def simple_runtime_spec():
    """创建简单的运行时规格"""
    return RuntimeSpec.simple(python_version=None, requirements=[])


@pytest.fixture
def temp_script_dir():
    """创建临时脚本目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestConcurrentExecution:
    """多任务并发执行测试"""

    @pytest.mark.asyncio
    async def test_concurrent_task_execution(self, temp_script_dir):
        """
        测试多任务并发执行

        验证：
        1. 多个任务可以同时执行
        2. 并发限制生效
        3. 所有任务都能正确完成
        """
        # 创建测试脚本
        script_content = '''
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

task_id = sys.argv[1] if len(sys.argv) > 1 else "unknown"
logger.info(f"Task {task_id} started")
time.sleep(0.5)  # 模拟工作
logger.info(f"Task {task_id} completed")
'''
        script_path = os.path.join(temp_script_dir, "concurrent_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器（并发限制为 3）
        config = ExecutorConfig(max_concurrent=3, default_timeout=30)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            # 创建运行时句柄（使用系统 Python）
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-concurrent",
                python_executable=sys.executable,
                python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
            )

            # 创建 5 个并发任务
            tasks = []
            for i in range(5):
                exec_plan = ExecPlan(
                    command=script_path,
                    args=[str(i)],
                    env={},
                    cwd=temp_script_dir,
                    timeout_seconds=30,
                    plugin_name=f"task-{i}",
                )
                task = asyncio.create_task(
                    executor.run(exec_plan, runtime_handle, NoOpLogSink())
                )
                tasks.append(task)

            # 等待所有任务完成
            start_time = time.time()
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start_time

            # 验证所有任务都成功
            for i, result in enumerate(results):
                assert result.status == RunStatus.SUCCESS, f"Task {i} failed: {result.error_message}"
                assert result.exit_code == 0, f"Task {i} exit code: {result.exit_code}"

            # 验证并发执行（5 个任务，每个 0.5s，并发 3，应该约 1s 完成）
            # 允许一些误差
            assert elapsed < 3.0, f"Tasks took too long: {elapsed}s (expected < 3s)"
            logger.info("5 个并发任务完成，耗时: {:.2f}s", elapsed)

            # 验证统计信息
            stats = executor.get_stats()
            assert stats["total_executions"] == 5
            assert stats["completed"] == 5

        finally:
            await executor.stop()

    @pytest.mark.asyncio
    async def test_concurrent_limit_enforcement(self, temp_script_dir):
        """
        测试并发限制强制执行

        验证：
        1. 并发数不超过配置的最大值
        2. 超出限制的任务会等待
        """
        # 创建长时间运行的脚本
        script_content = '''
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info("Starting long task")
time.sleep(2)
logger.info("Long task done")
'''
        script_path = os.path.join(temp_script_dir, "long_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器（并发限制为 2）
        config = ExecutorConfig(max_concurrent=2, default_timeout=30)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-limit",
                python_executable=sys.executable,
            )

            # 启动 4 个任务
            tasks = []
            for i in range(4):
                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=temp_script_dir,
                    timeout_seconds=30,
                    plugin_name=f"long-task-{i}",
                )
                task = asyncio.create_task(
                    executor.run(exec_plan, runtime_handle, NoOpLogSink())
                )
                tasks.append(task)

            # 等待一小段时间，检查并发数
            await asyncio.sleep(0.5)
            assert executor.running_count <= 2, f"Running count exceeded limit: {executor.running_count}"

            # 等待所有任务完成
            results = await asyncio.gather(*tasks)

            # 验证所有任务都成功
            for result in results:
                assert result.status == RunStatus.SUCCESS

            logger.info("并发限制验证通过，最大并发: {}", config.max_concurrent)

        finally:
            await executor.stop()


class TestRuntimeCacheReuse:
    """Runtime Cache 复用验证"""

    @pytest.mark.asyncio
    async def test_runtime_hash_determinism(self):
        """
        测试运行时哈希的确定性

        验证：
        1. 相同规格产生相同哈希
        2. 不同规格产生不同哈希
        3. 非确定性字段不影响哈希
        """
        # 创建两个相同的规格
        spec1 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements(["requests==2.31.0"]),
        )
        spec2 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements(["requests==2.31.0"]),
        )

        hash1 = compute_runtime_hash(spec1)
        hash2 = compute_runtime_hash(spec2)

        assert hash1 == hash2, "相同规格应产生相同哈希"
        logger.info("相同规格哈希: {}", hash1)

        # 创建不同的规格
        spec3 = RuntimeSpec(
            python_spec=PythonSpec(version="3.12"),
            lock_source=LockSource.from_requirements(["requests==2.31.0"]),
        )
        hash3 = compute_runtime_hash(spec3)

        assert hash1 != hash3, "不同规格应产生不同哈希"
        logger.info("不同规格哈希: {}", hash3)

        # 验证非确定性字段不影响哈希
        spec4 = spec1.with_env_vars({"SECRET_KEY": "test123"})
        hash4 = compute_runtime_hash(spec4)

        assert hash1 == hash4, "非确定性字段不应影响哈希"
        logger.info("非确定性字段验证通过")

    @pytest.mark.asyncio
    async def test_runtime_spec_equality(self):
        """
        测试运行时规格相等性

        验证：
        1. 相同确定性字段的规格相等
        2. 非确定性字段不影响相等性
        """
        spec1 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements(["flask==2.0.0"]),
            constraints=["werkzeug<3.0"],
        )

        spec2 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements(["flask==2.0.0"]),
            constraints=["werkzeug<3.0"],
            env_vars={"DEBUG": "true"},  # 非确定性字段
        )

        assert spec1 == spec2, "相同确定性字段的规格应相等"
        logger.info("规格相等性验证通过")

    @pytest.mark.asyncio
    async def test_requirements_order_independence(self):
        """
        测试 requirements 顺序无关性

        验证：
        1. 不同顺序的 requirements 产生相同哈希
        """
        spec1 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements([
                "requests==2.31.0",
                "flask==2.0.0",
                "numpy==1.24.0",
            ]),
        )

        spec2 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource.from_requirements([
                "numpy==1.24.0",
                "flask==2.0.0",
                "requests==2.31.0",
            ]),
        )

        hash1 = compute_runtime_hash(spec1)
        hash2 = compute_runtime_hash(spec2)

        assert hash1 == hash2, "不同顺序的 requirements 应产生相同哈希"
        logger.info("Requirements 顺序无关性验证通过: {}", hash1)


class TestTimeoutAndKill:
    """Timeout/Kill 行为验证"""

    @pytest.mark.asyncio
    async def test_task_timeout(self, temp_script_dir):
        """
        测试任务超时

        验证：
        1. 超时的任务被正确终止
        2. 返回正确的超时状态
        """
        # 创建长时间运行的脚本
        script_content = '''
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info("Starting infinite task")
while True:
    time.sleep(1)
    logger.info("Still running...")
'''
        script_path = os.path.join(temp_script_dir, "infinite_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器
        config = ExecutorConfig(max_concurrent=2, default_timeout=60)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-timeout",
                python_executable=sys.executable,
            )

            # 创建超时任务（2 秒超时）
            exec_plan = ExecPlan(
                command=script_path,
                args=[],
                env={},
                cwd=temp_script_dir,
                timeout_seconds=2,
                grace_period_seconds=1,
                plugin_name="timeout-task",
            )

            start_time = time.time()
            result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())
            elapsed = time.time() - start_time

            # 验证超时
            assert result.status == RunStatus.TIMEOUT, f"Expected TIMEOUT, got {result.status}"
            assert result.exit_code == 124, f"Expected exit code 124, got {result.exit_code}"
            assert elapsed < 5, f"Task took too long to timeout: {elapsed}s"

            logger.info("超时验证通过，耗时: {:.2f}s", elapsed)

        finally:
            await executor.stop()

    @pytest.mark.asyncio
    async def test_task_cancellation(self, temp_script_dir):
        """
        测试任务取消

        验证：
        1. 运行中的任务可以被取消
        2. 返回正确的取消状态
        """
        # 创建长时间运行的脚本
        script_content = '''
import logging
import signal
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

def handler(signum, frame):
    logger.info("Received signal, exiting gracefully")
    sys.exit(0)

signal.signal(signal.SIGTERM, handler)

logger.info("Starting cancellable task")
for i in range(60):
    time.sleep(1)
    logger.info(f"Running... {i}")
'''
        script_path = os.path.join(temp_script_dir, "cancellable_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器
        config = ExecutorConfig(max_concurrent=2, default_timeout=60, default_grace_period=5)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-cancel",
                python_executable=sys.executable,
            )

            exec_plan = ExecPlan(
                command=script_path,
                args=[],
                env={},
                cwd=temp_script_dir,
                timeout_seconds=60,
                grace_period_seconds=5,
                plugin_name="cancel-task",
            )

            # 启动任务
            task = asyncio.create_task(
                executor.run(exec_plan, runtime_handle, NoOpLogSink())
            )

            # 等待任务开始
            await asyncio.sleep(1)

            # 取消任务
            cancelled = await executor.cancel("cancel-task")
            assert cancelled, "取消操作应该成功"

            # 等待任务完成
            result = await task

            # 验证取消状态
            assert result.status == RunStatus.CANCELLED, f"Expected CANCELLED, got {result.status}"
            logger.info("任务取消验证通过")

        finally:
            await executor.stop()

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, temp_script_dir):
        """
        测试优雅关闭

        验证：
        1. 关闭时等待运行中的任务
        2. 超过 grace period 后强制终止
        """
        # 创建长时间运行的脚本
        script_content = '''
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

logger.info("Starting task")
for i in range(30):
    time.sleep(1)
    logger.info(f"Running... {i}")
logger.info("Task completed")
'''
        script_path = os.path.join(temp_script_dir, "shutdown_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器
        config = ExecutorConfig(max_concurrent=2, default_timeout=60)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-shutdown",
                python_executable=sys.executable,
            )

            exec_plan = ExecPlan(
                command=script_path,
                args=[],
                env={},
                cwd=temp_script_dir,
                timeout_seconds=60,
                plugin_name="shutdown-task",
            )

            # 启动任务
            task = asyncio.create_task(
                executor.run(exec_plan, runtime_handle, NoOpLogSink())
            )

            # 等待任务开始
            await asyncio.sleep(1)
            assert executor.running_count == 1, "应该有 1 个运行中的任务"

            # 优雅关闭（grace period 2 秒）
            start_time = time.time()
            await executor.stop(grace_period=2.0)
            elapsed = time.time() - start_time

            # 验证关闭时间
            assert elapsed < 5, f"Shutdown took too long: {elapsed}s"
            assert executor.running_count == 0, "关闭后不应有运行中的任务"

            logger.info("优雅关闭验证通过，耗时: {:.2f}s", elapsed)

        except Exception as e:
            await executor.stop(grace_period=1.0)
            raise


class TestExecutorStats:
    """执行器统计信息测试"""

    @pytest.mark.asyncio
    async def test_executor_statistics(self, temp_script_dir):
        """
        测试执行器统计信息

        验证：
        1. 统计信息正确更新
        2. 包含所有必要字段
        """
        # 创建成功和失败的脚本
        success_script = os.path.join(temp_script_dir, "success.py")
        with open(success_script, "w") as f:
            f.write("import logging\n")
            f.write("import sys\n")
            f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
            f.write("logger = logging.getLogger(__name__)\n")
            f.write('logger.info("Success")\n')

        fail_script = os.path.join(temp_script_dir, "fail.py")
        with open(fail_script, "w") as f:
            f.write("import logging\n")
            f.write("import sys\n")
            f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
            f.write("logger = logging.getLogger(__name__)\n")
            f.write('logger.info("Fail")\n')
            f.write("sys.exit(1)\n")

        # 创建执行器
        config = ExecutorConfig(max_concurrent=5, default_timeout=30)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-stats",
                python_executable=sys.executable,
            )

            # 执行成功任务
            for i in range(3):
                exec_plan = ExecPlan(
                    command=success_script,
                    args=[],
                    env={},
                    cwd=temp_script_dir,
                    plugin_name=f"success-{i}",
                )
                result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())
                assert result.status == RunStatus.SUCCESS

            # 执行失败任务
            for i in range(2):
                exec_plan = ExecPlan(
                    command=fail_script,
                    args=[],
                    env={},
                    cwd=temp_script_dir,
                    plugin_name=f"fail-{i}",
                )
                result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())
                assert result.status == RunStatus.FAILED

            # 验证统计信息
            stats = executor.get_stats()
            assert stats["total_executions"] == 5
            assert stats["completed"] == 3
            assert stats["failed"] == 2
            assert stats["max_concurrent"] == 5
            assert "available_slots" in stats

            logger.info("统计信息验证通过: {}", stats)

        finally:
            await executor.stop()


class TestLogCapture:
    """日志捕获测试"""

    @pytest.mark.asyncio
    async def test_stdout_stderr_capture(self, temp_script_dir):
        """
        测试 stdout/stderr 捕获

        验证：
        1. stdout 正确捕获
        2. stderr 正确捕获
        3. 日志条目包含正确的流类型
        """
        from antcode_worker.executor.base import CallbackLogSink
        from antcode_worker.domain.enums import LogStream

        # 创建输出脚本
        script_content = '''
import logging
import sys

formatter = logging.Formatter("%(message)s")

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)

stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(formatter)

stdout_logger = logging.getLogger("stdout_logger")
stdout_logger.setLevel(logging.INFO)
stdout_logger.handlers = [stdout_handler]
stdout_logger.propagate = False

stderr_logger = logging.getLogger("stderr_logger")
stderr_logger.setLevel(logging.INFO)
stderr_logger.handlers = [stderr_handler]
stderr_logger.propagate = False

stdout_logger.info("This is stdout")
stdout_logger.info("Another stdout line")
stderr_logger.info("This is stderr")
stderr_logger.info("Another stderr line")
'''
        script_path = os.path.join(temp_script_dir, "output_task.py")
        with open(script_path, "w") as f:
            f.write(script_content)

        # 创建执行器
        config = ExecutorConfig(max_concurrent=2, default_timeout=30)
        executor = ProcessExecutor(config)
        await executor.start()

        try:
            import sys as sys_module
            runtime_handle = RuntimeHandle(
                path=temp_script_dir,
                runtime_hash="test-logs",
                python_executable=sys_module.executable,
            )

            # 收集日志
            logs = []

            def log_callback(entry):
                logs.append(entry)

            log_sink = CallbackLogSink(log_callback)

            exec_plan = ExecPlan(
                command=script_path,
                args=[],
                env={},
                cwd=temp_script_dir,
                plugin_name="log-task",
            )

            result = await executor.run(exec_plan, runtime_handle, log_sink)

            # 验证执行成功
            assert result.status == RunStatus.SUCCESS

            # 验证日志捕获
            stdout_logs = [l for l in logs if l.stream == LogStream.STDOUT]
            stderr_logs = [l for l in logs if l.stream == LogStream.STDERR]

            assert len(stdout_logs) >= 2, f"Expected at least 2 stdout logs, got {len(stdout_logs)}"
            assert len(stderr_logs) >= 2, f"Expected at least 2 stderr logs, got {len(stderr_logs)}"

            # 验证内容
            stdout_content = " ".join(l.content for l in stdout_logs)
            assert "This is stdout" in stdout_content

            stderr_content = " ".join(l.content for l in stderr_logs)
            assert "This is stderr" in stderr_content

            logger.info(
                "日志捕获验证通过: stdout={} stderr={}",
                len(stdout_logs),
                len(stderr_logs),
            )

        finally:
            await executor.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
