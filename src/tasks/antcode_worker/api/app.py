"""FastAPI 应用"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .deps import get_engine, set_engine
from .routes import node_router, envs_router, projects_router, tasks_router, spider_router, queue_router
from .routes.queue import init_queue_components, shutdown_queue_components
from ..config import get_node_config, MACHINE_CODE_FILE
from ..core import EngineConfig, init_worker_engine
from ..services import local_env_service, local_project_service, master_client, node_ws_client
# 导入旧的 communication_manager 用于兼容性
from ..services import communication_manager as legacy_communication_manager
# 导入新的 gRPC 通信管理器
from ..transport import CommunicationManager as GrpcCommunicationManager
from ..domain.models import ConnectionConfig, Protocol

# 仅在首次断连时提示，避免日志刷屏
_disconnected_log_notified = set()

# gRPC 通信管理器实例
_grpc_communication_manager: GrpcCommunicationManager | None = None


def get_grpc_communication_manager() -> GrpcCommunicationManager | None:
    """获取 gRPC 通信管理器实例"""
    return _grpc_communication_manager


def set_grpc_communication_manager(manager: GrpcCommunicationManager):
    """设置 gRPC 通信管理器实例"""
    global _grpc_communication_manager
    _grpc_communication_manager = manager


# 兼容性包装器：根据配置选择使用哪个通信管理器
class CommunicationManagerWrapper:
    """通信管理器包装器，提供统一接口"""
    
    def __init__(self):
        self._use_grpc = False
    
    @property
    def is_connected(self) -> bool:
        if self._use_grpc and _grpc_communication_manager:
            return _grpc_communication_manager.is_connected
        return legacy_communication_manager.is_connected
    
    @property
    def current_protocol(self):
        if self._use_grpc and _grpc_communication_manager:
            return _grpc_communication_manager.current_protocol
        return legacy_communication_manager.current_protocol
    
    async def report_task_status(self, execution_id: str, status: str, exit_code: int = None, error_message: str = None):
        if self._use_grpc and _grpc_communication_manager:
            from ..domain.models import TaskStatus
            from datetime import datetime
            task_status = TaskStatus(
                execution_id=execution_id,
                status=status,
                exit_code=exit_code,
                error_message=error_message,
                timestamp=datetime.now(),
            )
            return await _grpc_communication_manager.send_task_status(task_status)
        return await legacy_communication_manager.report_task_status(execution_id, status, exit_code, error_message)
    
    async def report_log(self, execution_id: str, log_type: str, content: str):
        if self._use_grpc and _grpc_communication_manager:
            from ..domain.models import LogEntry
            from datetime import datetime
            log_entry = LogEntry(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
                timestamp=datetime.now(),
            )
            return await _grpc_communication_manager.send_logs([log_entry])
        return await legacy_communication_manager.report_log(execution_id, log_type, content)
    
    async def disconnect(self):
        """
        断开连接
        
        优雅关闭流程：
        1. 刷新待处理的日志
        2. 关闭 gRPC 流
        3. 断开通道
        
        Requirements: 7.4
        """
        if self._use_grpc and _grpc_communication_manager:
            logger.info("正在优雅关闭 gRPC 通信管理器...")
            await _grpc_communication_manager.disconnect()
        await legacy_communication_manager.disconnect()
    
    def set_use_grpc(self, use_grpc: bool):
        self._use_grpc = use_grpc


# 全局通信管理器包装器
communication_manager = CommunicationManagerWrapper()


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

    if communication_manager.is_connected:
        try:
            result = await communication_manager.report_task_status(report_id, "running")
            logger.debug(f"状态上报结果: {result}")
        except Exception as e:
            logger.warning(f"上报任务状态失败: {e}")
    else:
        logger.warning(f"节点未连接主站，任务状态未上报: {execution_id}")


async def on_task_complete(task: dict):
    """任务完成回调"""
    execution_id = task.get("execution_id", task.get("id", task.get("task_id")))
    status = task.get("status", "completed")
    report_id = task.get("master_execution_id") or execution_id

    logger.info(f"任务完成: {execution_id} [{status}], protocol={communication_manager.current_protocol.value}")

    if communication_manager.is_connected:
        try:
            result = await communication_manager.report_task_status(
                report_id, status, task.get("exit_code"), task.get("error_message")
            )
            logger.debug(f"状态上报结果: {result}")
        except Exception as e:
            logger.warning(f"上报任务完成状态失败: {e}")
    else:
        logger.warning(f"节点未连接主站，任务完成状态未上报: {execution_id}")


async def on_log_line(execution_id: str, log_type: str, content: str):
    """日志行回调"""
    if communication_manager.is_connected:
        await communication_manager.report_log(execution_id, log_type, content)
    else:
        if execution_id not in _disconnected_log_notified:
            _disconnected_log_notified.add(execution_id)
            logger.warning(f"节点未连接主站，日志未上报: {execution_id}")


async def on_task_cancel(data: dict):
    """远程取消任务回调（WebSocket）"""
    task_id = data.get("task_id")
    execution_id = data.get("execution_id")

    if not task_id:
        logger.warning("收到取消指令但缺少 task_id")
        return

    logger.info(f"收到远程取消指令: task_id={task_id}, execution_id={execution_id}")

    engine = get_engine()
    if engine:
        success = await engine.cancel_task(task_id)
        if success:
            logger.info(f"任务已取消: {task_id}")
            # 上报取消状态
            if communication_manager.is_connected and execution_id:
                await communication_manager.report_task_status(
                    execution_id, "cancelled", exit_code=-1, error_message="任务被远程取消"
                )
        else:
            logger.warning(f"取消任务失败（可能已完成或不存在）: {task_id}")


async def on_grpc_task_cancel(cancel):
    """
    gRPC 任务取消回调
    
    处理通过 gRPC 接收的任务取消请求。
    
    Args:
        cancel: TaskCancel 对象，包含 task_id 和 execution_id
    """
    from ..domain.models import TaskCancel
    
    task_id = cancel.task_id if isinstance(cancel, TaskCancel) else cancel.get("task_id", "")
    execution_id = cancel.execution_id if isinstance(cancel, TaskCancel) else cancel.get("execution_id", "")

    if not task_id:
        logger.warning("收到 gRPC 取消指令但缺少 task_id")
        return

    logger.info(f"收到 gRPC 远程取消指令: task_id={task_id}, execution_id={execution_id}")

    engine = get_engine()
    grpc_manager = get_grpc_communication_manager()
    
    if engine:
        success = await engine.cancel_task(task_id)
        if success:
            logger.info(f"任务已取消: {task_id}")
            # 发送取消确认
            if grpc_manager:
                await grpc_manager.send_cancel_ack(task_id, True)
            # 上报取消状态
            if communication_manager.is_connected and execution_id:
                await communication_manager.report_task_status(
                    execution_id, "cancelled", exit_code=-1, error_message="任务被远程取消"
                )
        else:
            logger.warning(f"取消任务失败（可能已完成或不存在）: {task_id}")
            if grpc_manager:
                await grpc_manager.send_cancel_ack(task_id, False, "任务不存在或已完成")


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

    # 注册 WebSocket 事件处理器
    node_ws_client.on("task_cancel", on_task_cancel)

    # 初始化队列组件（绑定到引擎的调度器）
    await init_queue_components()

    # 初始化 gRPC 通信管理器（如果启用）
    # Requirements: 8.2 - 初始化 CommunicationManager 并使用 gRPC 偏好连接
    if config.grpc_enabled and config.master_url:
        grpc_manager = GrpcCommunicationManager()
        set_grpc_communication_manager(grpc_manager)
        
        # 创建连接配置
        connection_config = ConnectionConfig(
            master_url=config.master_url,
            node_id=config.machine_code,  # 使用机器码作为节点 ID
            api_key=config.api_key or "",
            machine_code=config.machine_code,
            secret_key=config.secret_key,
            grpc_port=config.grpc_port,
            prefer_grpc=config.prefer_grpc,
            heartbeat_interval=config.heartbeat_interval,
            reconnect_base_delay=config.grpc_reconnect_base_delay,
            reconnect_max_delay=config.grpc_reconnect_max_delay,
        )
        
        # 注册任务取消回调
        grpc_manager.on_task_cancel(on_grpc_task_cancel)
        
        # 尝试连接
        try:
            connected = await grpc_manager.connect(connection_config)
            if connected:
                communication_manager.set_use_grpc(True)
                logger.info(f"gRPC 通信管理器已连接，协议: {grpc_manager.current_protocol.value}")
            else:
                logger.warning("gRPC 通信管理器连接失败，将使用传统通信方式")
        except Exception as e:
            logger.error(f"gRPC 通信管理器初始化失败: {e}")
    else:
        if not config.grpc_enabled:
            logger.info("gRPC 通信已禁用，使用传统通信方式")
        elif not config.master_url:
            logger.debug("未配置 master_url，跳过 gRPC 通信管理器初始化")

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
