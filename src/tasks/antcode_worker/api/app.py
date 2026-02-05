"""FastAPI 应用"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .deps import get_engine, set_engine
from .routes import node_router, envs_router, projects_router, tasks_router, spider_router, queue_router
from .routes.queue import init_queue_components, shutdown_queue_components
from ..config import get_node_config, MACHINE_CODE_FILE
from ..core import EngineConfig, init_worker_engine
from ..services import local_env_service, local_project_service, master_client
from ..transport.manager import communication_manager
from ..domain.models import ConnectionConfig, LogEntry, TaskStatus, TaskCancel

# 仅在首次断连时提示，避免日志刷屏
_disconnected_log_notified = set()


def print_banner(config):
    """打印启动横幅"""
    code_status = "已持久化" if MACHINE_CODE_FILE.exists() else "新生成"
    print(f"""
  AntCode Worker Node v2.1
  ========================
  节点名称: {config.name}
  节点地址: {config.host}:{config.port}
  所属区域: {config.region}
  机器码:   {config.machine_code} ({code_status})
    """)


async def on_task_start(task: dict):
    """任务开始回调"""
    execution_id = task.get("execution_id", task.get("id", task.get("task_id")))
    report_id = task.get("master_execution_id") or execution_id

    logger.info(f"任务启动: {execution_id}, protocol={communication_manager.current_protocol.value}")

    if not communication_manager.is_connected:
        logger.warning(f"节点未连接主站，任务状态未上报: {execution_id}")
        return

    try:
        ok = await communication_manager.send_task_status(TaskStatus(
            execution_id=report_id,
            status="running",
            timestamp=datetime.now(),
        ))
        logger.debug(f"状态上报结果: {ok}")
    except Exception as e:
        logger.warning(f"上报任务状态失败: {e}")


async def on_task_complete(task: dict):
    """任务完成回调"""
    execution_id = task.get("execution_id", task.get("id", task.get("task_id")))
    status = task.get("status", "success")
    report_id = task.get("master_execution_id") or execution_id

    logger.info(f"任务完成: {execution_id} [{status}], protocol={communication_manager.current_protocol.value}")

    if not communication_manager.is_connected:
        logger.warning(f"节点未连接主站，任务完成状态未上报: {execution_id}")
        return

    try:
        ok = await communication_manager.send_task_status(TaskStatus(
            execution_id=report_id,
            status=status,
            exit_code=task.get("exit_code"),
            error_message=task.get("error_message"),
            timestamp=datetime.now(),
        ))
        logger.debug(f"状态上报结果: {ok}")
    except Exception as e:
        logger.warning(f"上报任务完成状态失败: {e}")


async def on_log_line(execution_id: str, log_type: str, content: str):
    """日志行回调"""
    if communication_manager.is_connected:
        try:
            await communication_manager.send_logs([LogEntry(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
                timestamp=datetime.now(),
            )])
        except Exception as e:
            logger.debug(f"日志上报失败: {e}")
    else:
        if execution_id not in _disconnected_log_notified:
            _disconnected_log_notified.add(execution_id)
            logger.warning(f"节点未连接主站，日志未上报: {execution_id}")

async def on_task_cancel(cancel: TaskCancel):
    """远程取消任务回调（来自 Master）"""
    task_id = getattr(cancel, "task_id", None)
    execution_id = getattr(cancel, "execution_id", None)

    if not task_id:
        logger.warning("收到取消指令但缺少 task_id")
        return

    logger.info(f"收到远程取消指令: task_id={task_id}, execution_id={execution_id}")

    engine = get_engine()
    if not engine:
        await communication_manager.send_cancel_ack(task_id, False, "engine not ready")
        return

    success = await engine.cancel_task(task_id)
    if success:
        await communication_manager.send_cancel_ack(task_id, True)
        if execution_id and communication_manager.is_connected:
            await communication_manager.send_task_status(TaskStatus(
                execution_id=execution_id,
                status="cancelled",
                exit_code=-1,
                error_message="任务被远程取消",
                timestamp=datetime.now(),
            ))
    else:
        await communication_manager.send_cancel_ack(task_id, False, "任务不存在或已完成")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    config = get_node_config()

    # 初始化基础服务
    local_env_service.set_venvs_dir(config.venvs_dir)
    local_project_service.set_projects_dir(config.projects_dir)

    # 初始化引擎配置
    engine_config = EngineConfig(
        max_queue_size=10000,
        max_concurrent_tasks=config.max_concurrent_tasks,
        task_timeout=config.task_timeout,
    )

    # 初始化引擎
    engine = init_worker_engine(engine_config)
    set_engine(engine)

    # 设置引擎服务（项目服务、环境服务、主控客户端）
    engine.set_services(
        project_service=local_project_service,
        env_service=local_env_service,
        master_client=master_client
    )

    # 设置引擎回调
    engine.set_callbacks(
        on_task_start=on_task_start,
        on_task_complete=on_task_complete,
        on_log_line=on_log_line
    )

    # 启动引擎（这会启动引擎内部的调度器）
    await engine.start()

    # 初始化队列组件（绑定到引擎的调度器）
    await init_queue_components()

    # 注册 Master -> Worker 回调
    communication_manager.on_task_cancel(on_task_cancel)

    # 如果已配置连接信息，则启动时自动连接（主要用于容器/自动化场景）
    if config.grpc_enabled and config.master_url and config.api_key and config.access_token and getattr(config, "node_id", None):
        connection_config = ConnectionConfig(
            master_url=config.master_url,
            node_id=config.node_id,
            api_key=config.api_key,
            machine_code=config.machine_code,
            access_token=config.access_token,
            secret_key=config.secret_key,
            grpc_port=config.grpc_port,
            prefer_grpc=config.prefer_grpc,
            heartbeat_interval=config.heartbeat_interval,
            reconnect_base_delay=config.grpc_reconnect_base_delay,
            reconnect_max_delay=config.grpc_reconnect_max_delay,
        )

        try:
            await master_client.connect(
                master_url=config.master_url,
                machine_code=config.machine_code,
                api_key=config.api_key,
                access_token=config.access_token,
                secret_key=config.secret_key,
                node_id=config.node_id,
            )
        except Exception as e:
            logger.warning(f"Master HTTP 连接初始化失败: {e}")

        try:
            connected = await communication_manager.connect(connection_config)
            if connected:
                logger.info(f"通信管理器已连接，协议: {communication_manager.current_protocol.value}")
            else:
                logger.warning("通信管理器连接失败")
        except Exception as e:
            logger.error(f"通信管理器初始化失败: {e}")

    # 启动资源监控（如果启用）
    if config.auto_resource_limit:
        from ..services.resource_monitor import resource_monitor
        await resource_monitor.start_monitoring()
        logger.info(f"自适应资源限制已启用: 并发={config.max_concurrent_tasks}, 内存={config.task_memory_limit_mb}MB")

    print_banner(config)
    print(f"健康检查: http://{config.host}:{config.port}/health")
    print(f"API 文档: http://{config.host}:{config.port}/docs\n")

    yield

    # 关闭资源监控
    if config.auto_resource_limit:
        try:
            from ..services.resource_monitor import resource_monitor
            await resource_monitor.stop_monitoring()
        except Exception as e:
            logger.error(f"停止资源监控失败: {e}")

    # 关闭
    await engine.stop()
    await shutdown_queue_components()
    await communication_manager.disconnect()
    try:
        await master_client.disconnect()
    except Exception:
        pass
    logger.info("节点已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    application = FastAPI(
        title="AntCode Worker Node",
        description="分布式任务执行节点 - 优先级调度架构",
        version="2.1.0",
        lifespan=lifespan
    )

    # CORS 配置：从环境变量读取允许的来源，默认仅允许本地开发
    import os
    cors_origins_str = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 健康检查
    @application.get("/health", tags=["系统"])
    async def health_check():
        config = get_node_config()
        engine = get_engine()
        return {
            "status": "healthy",
            "name": config.name,
            "machine_code": config.machine_code,
            "version": config.version,
            "engine_state": engine.state.name,
            "is_connected": communication_manager.is_connected,
            "protocol": communication_manager.current_protocol.value,
        }

    # 注册路由
    application.include_router(node_router)
    application.include_router(envs_router)
    application.include_router(projects_router)
    application.include_router(tasks_router)
    application.include_router(spider_router)
    application.include_router(queue_router)

    return application


# 默认应用实例
app = create_app()
