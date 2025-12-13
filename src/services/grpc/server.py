"""
gRPC 服务器模块

提供 Master 端 gRPC 服务器实现。
"""
import asyncio
from concurrent import futures
from typing import Optional

import grpc
from grpc import aio as grpc_aio
from loguru import logger

from src.services.grpc.config import GrpcConfig, grpc_config
from src.grpc_generated import add_NodeServiceServicer_to_server


class GrpcServer:
    """gRPC 服务器
    
    管理 gRPC 服务器的生命周期，包括启动、停止和服务注册。
    """
    
    def __init__(self, config: Optional[GrpcConfig] = None):
        """初始化 gRPC 服务器
        
        Args:
            config: gRPC 配置，默认使用全局配置
        """
        self.config = config or grpc_config
        self._server: Optional[grpc_aio.Server] = None
        self._started = False
        self._servicer = None
    
    @property
    def is_running(self) -> bool:
        """服务器是否正在运行"""
        return self._started and self._server is not None
    
    @property
    def port(self) -> int:
        """服务器端口"""
        return self.config.port
    
    def set_servicer(self, servicer) -> None:
        """设置 NodeService 实现
        
        Args:
            servicer: NodeServiceServicer 实现
        """
        self._servicer = servicer
    
    async def start(self) -> bool:
        """启动 gRPC 服务器
        
        Returns:
            是否成功启动
        """
        if not self.config.enabled:
            logger.info("gRPC 服务已禁用，跳过启动")
            return False
        
        if self._started:
            logger.warning("gRPC 服务器已在运行")
            return True
        
        try:
            # 创建服务器
            self._server = grpc_aio.server(
                futures.ThreadPoolExecutor(max_workers=self.config.max_workers),
                options=self.config.server_options,
            )
            
            # 注册服务
            if self._servicer is not None:
                add_NodeServiceServicer_to_server(self._servicer, self._server)
                logger.debug("已注册 NodeService 服务")
            else:
                logger.warning("未设置 NodeService 实现，服务器将无法处理请求")
            
            # 绑定端口
            listen_addr = f"[::]:{self.config.port}"
            
            if self.config.tls_enabled:
                # TLS 模式
                with open(self.config.tls_cert_path, "rb") as f:
                    cert = f.read()
                with open(self.config.tls_key_path, "rb") as f:
                    key = f.read()
                
                credentials = grpc.ssl_server_credentials([(key, cert)])
                self._server.add_secure_port(listen_addr, credentials)
                logger.info(f"gRPC 服务器启动于 {listen_addr} (TLS)")
            else:
                # 非 TLS 模式
                self._server.add_insecure_port(listen_addr)
                logger.info(f"gRPC 服务器启动于 {listen_addr}")
            
            # 启动服务器
            await self._server.start()
            self._started = True
            
            logger.info(
                f"gRPC 服务器已启动 - 端口: {self.config.port}, "
                f"最大工作线程: {self.config.max_workers}"
            )
            return True
            
        except Exception as e:
            from src.services.grpc.metrics import log_grpc_error
            log_grpc_error(
                error_message=str(e),
                operation="server_start",
                extra_context={"port": self.config.port},
            )
            self._server = None
            self._started = False
            return False
    
    async def stop(self, grace_period: float = 5.0) -> None:
        """停止 gRPC 服务器
        
        Args:
            grace_period: 优雅关闭等待时间（秒）
        """
        if not self._started or self._server is None:
            logger.debug("gRPC 服务器未运行，无需停止")
            return
        
        try:
            logger.info(f"正在停止 gRPC 服务器，优雅关闭等待 {grace_period} 秒...")
            
            # 优雅关闭
            await self._server.stop(grace_period)
            
            self._started = False
            self._server = None
            
            logger.info("gRPC 服务器已停止")
            
        except Exception as e:
            from src.services.grpc.metrics import log_grpc_error
            log_grpc_error(
                error_message=str(e),
                operation="server_stop",
                extra_context={"port": self.config.port},
            )
            self._started = False
            self._server = None
    
    async def wait_for_termination(self) -> None:
        """等待服务器终止"""
        if self._server is not None:
            await self._server.wait_for_termination()


# 全局服务器实例
grpc_server: Optional[GrpcServer] = None


def get_grpc_server() -> GrpcServer:
    """获取全局 gRPC 服务器实例"""
    global grpc_server
    if grpc_server is None:
        grpc_server = GrpcServer()
    return grpc_server
