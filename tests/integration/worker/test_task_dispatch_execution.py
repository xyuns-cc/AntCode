"""
任务分发和执行集成测试

验证 Direct 模式和 Gateway 模式的 E2E 流程。

Requirements: 14.2
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime

import pytest
from loguru import logger

# 从环境变量或默认值获取 Redis URL
REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_i36zi5@154.12.30.182:6379/0")


@pytest.fixture
def unique_task_id():
    """生成唯一任务 ID"""
    return f"dispatch-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_execution_id():
    """生成唯一执行 ID"""
    return f"dispatch-exec-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"dispatch-worker-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_stream_prefix():
    """生成唯一 stream 前缀"""
    return f"test:dispatch:{uuid.uuid4().hex[:8]}:"


@pytest.mark.integration
class TestDirectModeTaskDispatch:
    """Direct 模式任务分发测试 - Requirements: 14.2"""

    @pytest.mark.asyncio
    async def test_master_dispatch_worker_receive(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试 Master 分发任务，Worker 接收

        验证：
        1. Master 可以将任务写入 Redis Stream
        2. Worker 可以从 Stream 拉取任务
        3. 任务数据完整传递
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport, ServerConfig
        from antcode_worker.transport.redis.keys import RedisKeys

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(redis_url=REDIS_URL)
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )
        keys = RedisKeys()

        try:
            # 启动 Transport
            await transport.start()

            # 确保 consumer group 存在
            stream_key = keys.task_ready_stream(unique_worker_id)
            try:
                await redis_client.xgroup_create(
                    stream_key,
                    keys.consumer_group_name(),
                    id="0",
                    mkstream=True,
                )
            except Exception:
                pass

            # 模拟 Master 分发任务
            task_data = {
                "task_id": unique_task_id,
                "project_id": "test-project-001",
                "project_type": "code",
                "priority": "10",
                "timeout": "120",
                "entry_point": "main.py",
                "download_url": "/tmp/test-project",
                "file_hash": "abc123def456",
            }

            msg_id = await redis_client.xadd(stream_key, task_data)
            assert msg_id, "任务写入失败"
            logger.info("Master 分发任务: {} msg_id={}", unique_task_id, msg_id)

            # Worker 拉取任务
            task = await transport.poll_task(timeout=5.0)
            assert task is not None, "Worker 未能拉取到任务"
            assert task.task_id == unique_task_id
            assert task.project_id == "test-project-001"
            assert task.project_type == "code"
            assert task.priority == 10
            assert task.timeout == 120
            assert task.entry_point == "main.py"

            logger.info("Worker 接收任务: {}", task.task_id)
            logger.info("Direct 模式任务分发验证通过")

        finally:
            await transport.stop()
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_task_execution_with_executor(self, unique_task_id, unique_execution_id):
        """
        测试任务执行

        验证：
        1. Executor 可以执行 Python 脚本
        2. 输出被正确捕获
        3. 退出码正确返回
        """
        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import CallbackLogSink, ExecutorConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试脚本
            script_path = os.path.join(tmpdir, "test_script.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import sys\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('logger.info("Task execution test")\n')
                f.write('logger.info("Processing data...")\n')
                f.write('result = 1 + 1\n')
                f.write('logger.info(f"Result: {result}")\n')
                f.write('sys.exit(0)\n')

            # 创建执行器
            config = ExecutorConfig(max_concurrent=2, default_timeout=30)
            executor = ProcessExecutor(config)
            await executor.start()

            # 收集日志
            logs = []

            def log_callback(entry):
                logs.append(entry)

            log_sink = CallbackLogSink(log_callback)

            try:
                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-exec",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=tmpdir,
                    timeout_seconds=30,
                    plugin_name=unique_execution_id,
                )

                result = await executor.run(exec_plan, runtime_handle, log_sink)

                # 验证结果
                assert result.status == RunStatus.SUCCESS
                assert result.exit_code == 0

                # 验证日志
                from antcode_worker.domain.enums import LogStream
                stdout_logs = [l for l in logs if l.stream == LogStream.STDOUT]
                assert len(stdout_logs) >= 3
                assert any("Task execution test" in l.content for l in stdout_logs)
                assert any("Result: 2" in l.content for l in stdout_logs)

                logger.info("任务执行成功: exit_code={}", result.exit_code)
                logger.info("日志行数: {}", len(stdout_logs))

            finally:
                await executor.stop()

    @pytest.mark.asyncio
    async def test_direct_mode_full_e2e(
        self,
        unique_task_id,
        unique_execution_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        Direct 模式完整 E2E 测试

        验证完整流程：
        1. Master 分发任务到 Redis
        2. Worker Transport 拉取任务
        3. Worker Executor 执行任务
        4. Worker Transport 上报结果
        5. Worker Transport ACK 任务
        """
        import redis.asyncio as aioredis

        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink
        from antcode_worker.transport import RedisTransport, ServerConfig, TaskResult
        from antcode_worker.transport.redis.keys import RedisKeys

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(redis_url=REDIS_URL)
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )
        keys = RedisKeys()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试脚本
            script_path = os.path.join(tmpdir, "main.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import sys\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('logger.info("E2E Direct Mode Test")\n')
                f.write('logger.info("Task completed")\n')

            try:
                # 1. 启动 Transport
                await transport.start()
                logger.info("Transport 已启动")

                # 确保 consumer group 存在
                stream_key = keys.task_ready_stream(unique_worker_id)
                try:
                    await redis_client.xgroup_create(
                        stream_key,
                        keys.consumer_group_name(),
                        id="0",
                        mkstream=True,
                    )
                except Exception:
                    pass

                # 2. Master 分发任务
                task_data = {
                    "task_id": unique_task_id,
                    "project_id": "e2e-project",
                    "project_type": "code",
                    "priority": "5",
                    "timeout": "60",
                    "entry_point": "main.py",
                    "download_url": tmpdir,
                    "file_hash": "",
                }
                await redis_client.xadd(stream_key, task_data)
                logger.info("任务已分发: {}", unique_task_id)

                # 3. Worker 拉取任务
                task = await transport.poll_task(timeout=5.0)
                assert task is not None
                assert task.task_id == unique_task_id
                logger.info("任务已拉取: {}", task.task_id)

                # 4. 执行任务
                executor_config = ExecutorConfig(max_concurrent=2, default_timeout=30)
                executor = ProcessExecutor(executor_config)
                await executor.start()

                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-e2e-direct",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=tmpdir,
                    timeout_seconds=task.timeout,
                    plugin_name=unique_execution_id,
                )

                exec_result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())
                await executor.stop()
                logger.info("执行完成: status={}", exec_result.status)

                # 5. 上报结果
                result = TaskResult(
                    run_id=unique_execution_id,
                    task_id=task.task_id,
                    status=exec_result.status.value,
                    exit_code=exec_result.exit_code or 0,
                    error_message=exec_result.error_message or "",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    duration_ms=exec_result.duration_ms,
                )
                report_success = await transport.report_result(result)
                assert report_success
                logger.info("结果已上报")

                # 6. ACK 任务
                ack_success = await transport.ack_task(task.receipt, accepted=True)
                assert ack_success
                logger.info("任务已 ACK")

                # 7. 验证结果
                assert exec_result.status == RunStatus.SUCCESS
                assert exec_result.exit_code == 0

                # 验证结果已写入 Redis
                result_key = keys.task_result_stream()
                results = await redis_client.xrevrange(result_key, "+", "-", count=10)
                found = any(d.get("task_id") == unique_task_id for _, d in results)
                assert found, "结果未写入 Redis"

                logger.info("Direct 模式完整 E2E 验证通过")

            finally:
                await transport.stop()
                try:
                    await redis_client.delete(stream_key)
                except Exception:
                    pass
                await redis_client.aclose()


@pytest.mark.integration
class TestGatewayModeTaskDispatch:
    """Gateway 模式任务分发测试 - Requirements: 14.2"""

    @pytest.mark.asyncio
    async def test_gateway_transport_task_decode(self, unique_task_id):
        """
        测试 Gateway 模式任务解码

        验证：
        1. 任务数据可以正确解码
        2. 字段映射正确
        """
        from antcode_worker.transport.gateway.codecs import TaskDecoder

        task_data = {
            "task_id": unique_task_id,
            "project_id": "gateway-project-001",
            "project_type": "spider",
            "priority": 8,
            "params": {"url": "https://example.com"},
            "environment": {"API_KEY": "test-key"},
            "timeout": 1800,
            "download_url": "https://storage.example.com/project.zip",
            "file_hash": "sha256:abc123",
            "entry_point": "spider.py",
        }

        task = TaskDecoder.decode_from_dict(task_data)

        assert task.task_id == unique_task_id
        assert task.project_id == "gateway-project-001"
        assert task.project_type == "spider"
        assert task.priority == 8
        assert task.params == {"url": "https://example.com"}
        assert task.environment == {"API_KEY": "test-key"}
        assert task.timeout == 1800
        assert task.entry_point == "spider.py"

        logger.info("Gateway 任务解码成功: {}", unique_task_id)

    @pytest.mark.asyncio
    async def test_gateway_transport_result_encode(self, unique_task_id):
        """
        测试 Gateway 模式结果编码

        验证：
        1. 结果可以正确编码
        2. 字段映射正确
        """
        from antcode_worker.transport.base import TaskResult
        from antcode_worker.transport.gateway.codecs import ResultEncoder

        result = TaskResult(
            run_id=f"run-{unique_task_id}",
            task_id=unique_task_id,
            status="success",
            exit_code=0,
            error_message="",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            duration_ms=2500.5,
            data={"items_scraped": 100},
        )

        worker_id = "gateway-worker-001"
        encoded = ResultEncoder.encode_to_dict(result, worker_id)

        assert encoded["task_id"] == unique_task_id
        assert encoded["worker_id"] == worker_id
        assert encoded["status"] == "success"
        assert encoded["exit_code"] == 0
        assert encoded["duration_ms"] == 2500
        assert encoded["data"] == {"items_scraped": 100}

        logger.info("Gateway 结果编码成功: {}", unique_task_id)

    @pytest.mark.asyncio
    async def test_gateway_mode_mock_e2e(self, unique_task_id, unique_execution_id):
        """
        Gateway 模式模拟 E2E 测试

        由于 Gateway 需要真实的 gRPC 服务器，这里使用模拟流程验证：
        1. Transport 初始化
        2. 任务解码
        3. 执行任务
        4. 结果编码
        5. 幂等性缓存
        """
        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink
        from antcode_worker.transport.base import TaskResult
        from antcode_worker.transport.gateway.codecs import ResultEncoder, TaskDecoder
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        worker_id = f"gateway-worker-{uuid.uuid4().hex[:8]}"

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试脚本
            script_path = os.path.join(tmpdir, "main.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import sys\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('logger.info("Gateway E2E Test")\n')

            # 1. 创建 Gateway Transport
            config = GatewayConfig(
                gateway_host="localhost",
                gateway_port=50051,
                use_tls=False,
                auth_method="api_key",
                api_key="test-api-key",
                worker_id=worker_id,
                enable_receipt_idempotency=True,
            )
            transport = GatewayTransport(gateway_config=config)
            logger.info("Gateway Transport 已创建: {}", worker_id)

            # 2. 模拟任务数据解码
            task_data = {
                "task_id": unique_task_id,
                "project_id": "gateway-e2e-project",
                "project_type": "code",
                "priority": 5,
                "timeout": 60,
                "entry_point": "main.py",
            }
            task = TaskDecoder.decode_from_dict(task_data)
            logger.info("任务已解码: {}", task.task_id)

            # 3. 执行任务
            executor_config = ExecutorConfig(max_concurrent=2, default_timeout=30)
            executor = ProcessExecutor(executor_config)
            await executor.start()

            runtime_handle = RuntimeHandle(
                path=tmpdir,
                runtime_hash="test-gateway-e2e",
                python_executable=sys.executable,
            )

            exec_plan = ExecPlan(
                command=script_path,
                args=[],
                env={},
                cwd=tmpdir,
                timeout_seconds=task.timeout,
                plugin_name=unique_execution_id,
            )

            exec_result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())
            await executor.stop()
            logger.info("执行完成: status={}", exec_result.status)

            # 4. 编码结果
            result = TaskResult(
                run_id=unique_execution_id,
                task_id=task.task_id,
                status=exec_result.status.value,
                exit_code=exec_result.exit_code or 0,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=exec_result.duration_ms,
            )
            encoded_result = ResultEncoder.encode_to_dict(result, worker_id)
            logger.info("结果已编码: status={}", encoded_result["status"])

            # 5. 测试幂等性缓存
            cache_key = f"result:{unique_task_id}"
            transport._cache_result(cache_key, True)
            cached = transport._get_cached_result(cache_key)
            assert cached is True
            logger.info("幂等性缓存验证成功")

            # 验证结果
            assert exec_result.status == RunStatus.SUCCESS
            assert exec_result.exit_code == 0

            logger.info("Gateway 模式模拟 E2E 验证通过")


@pytest.mark.integration
class TestTaskExecutionEdgeCases:
    """任务执行边界情况测试"""

    @pytest.mark.asyncio
    async def test_task_execution_timeout(self, unique_execution_id):
        """
        测试任务执行超时

        验证：
        1. 超时任务被正确终止
        2. 返回超时状态
        """
        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建会超时的脚本
            script_path = os.path.join(tmpdir, "timeout_script.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import sys\n")
                f.write("import time\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('logger.info("Starting long task...")\n')
                f.write('time.sleep(60)\n')  # 睡眠 60 秒
                f.write('logger.info("Should not reach here")\n')

            config = ExecutorConfig(max_concurrent=2, default_timeout=2)  # 2 秒超时
            executor = ProcessExecutor(config)
            await executor.start()

            try:
                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-timeout",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=tmpdir,
                    timeout_seconds=2,  # 2 秒超时
                    plugin_name=unique_execution_id,
                )

                result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())

                # 验证超时
                assert result.status == RunStatus.TIMEOUT
                logger.info("超时测试通过: status={}", result.status)

            finally:
                await executor.stop()

    @pytest.mark.asyncio
    async def test_task_execution_failure(self, unique_execution_id):
        """
        测试任务执行失败

        验证：
        1. 失败任务返回正确状态
        2. 退出码正确
        """
        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建会失败的脚本
            script_path = os.path.join(tmpdir, "fail_script.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import sys\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('logger.info("About to fail...")\n')
                f.write('sys.exit(1)\n')

            config = ExecutorConfig(max_concurrent=2, default_timeout=30)
            executor = ProcessExecutor(config)
            await executor.start()

            try:
                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-fail",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=tmpdir,
                    timeout_seconds=30,
                    plugin_name=unique_execution_id,
                )

                result = await executor.run(exec_plan, runtime_handle, NoOpLogSink())

                # 验证失败
                assert result.status == RunStatus.FAILED
                assert result.exit_code == 1
                logger.info("失败测试通过: exit_code={}", result.exit_code)

            finally:
                await executor.stop()

    @pytest.mark.asyncio
    async def test_task_with_environment_variables(self, unique_execution_id):
        """
        测试带环境变量的任务执行

        验证：
        1. 环境变量正确传递
        2. 脚本可以读取环境变量
        """
        from antcode_worker.domain.enums import LogStream, RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import CallbackLogSink, ExecutorConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "env_script.py")
            with open(script_path, "w") as f:
                f.write("import logging\n")
                f.write("import os\n")
                f.write("import sys\n")
                f.write("logging.basicConfig(level=logging.INFO, format=\"%(message)s\", stream=sys.stdout)\n")
                f.write("logger = logging.getLogger(__name__)\n")
                f.write('api_key = os.environ.get("TEST_API_KEY", "not_set")\n')
                f.write('secret = os.environ.get("TEST_SECRET", "not_set")\n')
                f.write('logger.info(f"API_KEY={api_key}")\n')
                f.write('logger.info(f"SECRET={secret}")\n')

            config = ExecutorConfig(max_concurrent=2, default_timeout=30)
            executor = ProcessExecutor(config)
            await executor.start()

            logs = []

            def log_callback(entry):
                logs.append(entry)

            log_sink = CallbackLogSink(log_callback)

            try:
                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-env",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={
                        "TEST_API_KEY": "my-api-key-123",
                        "TEST_SECRET": "super-secret-456",
                    },
                    cwd=tmpdir,
                    timeout_seconds=30,
                    plugin_name=unique_execution_id,
                )

                result = await executor.run(exec_plan, runtime_handle, log_sink)

                assert result.status == RunStatus.SUCCESS

                # 验证环境变量被正确传递
                stdout_logs = [l for l in logs if l.stream == LogStream.STDOUT]
                log_content = " ".join(l.content for l in stdout_logs)
                assert "my-api-key-123" in log_content
                assert "super-secret-456" in log_content

                logger.info("环境变量测试通过")

            finally:
                await executor.stop()
