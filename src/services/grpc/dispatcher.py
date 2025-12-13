"""
消息分发器模块

提供消息路由和处理器注册功能。
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Awaitable, Dict, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.grpc_generated import NodeMessage


@dataclass
class NodeContext:
    """节点上下文，包含连接相关信息"""
    
    node_id: str
    api_key: str
    connected_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def update_heartbeat(self) -> None:
        """更新最后心跳时间"""
        self.last_heartbeat = datetime.now()


class MessageHandler(ABC):
    """消息处理器抽象基类"""
    
    @abstractmethod
    async def handle(self, message: Any, context: NodeContext) -> Optional[Any]:
        """处理消息
        
        Args:
            message: 消息内容
            context: 节点上下文
            
        Returns:
            可选的响应消息
        """
        pass


class FunctionHandler(MessageHandler):
    """函数式消息处理器"""
    
    def __init__(self, func: Callable[[Any, NodeContext], Awaitable[Optional[Any]]]):
        self._func = func
    
    async def handle(self, message: Any, context: NodeContext) -> Optional[Any]:
        return await self._func(message, context)


class MessageDispatcher:
    """消息分发器
    
    负责将接收到的消息路由到对应的处理器。
    支持注册多种消息类型的处理器。
    """
    
    # 消息类型映射
    MESSAGE_TYPES = {
        "heartbeat": "heartbeat",
        "log_batch": "log_batch",
        "task_status": "task_status",
        "task_ack": "task_ack",
        "cancel_ack": "cancel_ack",
    }
    
    def __init__(self):
        self._handlers: Dict[str, MessageHandler] = {}
        self._default_handler: Optional[MessageHandler] = None
    
    def register(
        self,
        message_type: str,
        handler: MessageHandler | Callable[[Any, NodeContext], Awaitable[Optional[Any]]]
    ) -> None:
        """注册消息处理器
        
        Args:
            message_type: 消息类型
            handler: 处理器实例或异步函数
        """
        if callable(handler) and not isinstance(handler, MessageHandler):
            handler = FunctionHandler(handler)
        
        self._handlers[message_type] = handler
        logger.debug(f"已注册消息处理器: {message_type}")
    
    def register_default(
        self,
        handler: MessageHandler | Callable[[Any, NodeContext], Awaitable[Optional[Any]]]
    ) -> None:
        """注册默认处理器，用于处理未知消息类型
        
        Args:
            handler: 处理器实例或异步函数
        """
        if callable(handler) and not isinstance(handler, MessageHandler):
            handler = FunctionHandler(handler)
        
        self._default_handler = handler
        logger.debug("已注册默认消息处理器")
    
    def unregister(self, message_type: str) -> bool:
        """取消注册消息处理器
        
        Args:
            message_type: 消息类型
            
        Returns:
            是否成功取消注册
        """
        if message_type in self._handlers:
            del self._handlers[message_type]
            logger.debug(f"已取消注册消息处理器: {message_type}")
            return True
        return False
    
    def get_message_type(self, message: "NodeMessage") -> Optional[str]:
        """获取消息类型
        
        Args:
            message: NodeMessage 实例
            
        Returns:
            消息类型字符串，如果无法识别则返回 None
        """
        # 检查 oneof 字段
        which = message.WhichOneof("payload")
        return which
    
    def get_message_payload(self, message: "NodeMessage") -> Optional[Any]:
        """获取消息负载
        
        Args:
            message: NodeMessage 实例
            
        Returns:
            消息负载对象
        """
        message_type = self.get_message_type(message)
        if message_type:
            return getattr(message, message_type, None)
        return None
    
    async def dispatch(
        self,
        message: "NodeMessage",
        context: NodeContext
    ) -> Optional[Any]:
        """分发消息到对应处理器
        
        Args:
            message: NodeMessage 实例
            context: 节点上下文
            
        Returns:
            处理器返回的响应（如果有）
        """
        message_type = self.get_message_type(message)
        
        if message_type is None:
            logger.warning(f"无法识别的消息类型: {message}")
            return None
        
        payload = self.get_message_payload(message)
        
        # 查找处理器
        handler = self._handlers.get(message_type)
        
        if handler is None:
            if self._default_handler is not None:
                logger.debug(f"使用默认处理器处理消息类型: {message_type}")
                handler = self._default_handler
            else:
                logger.warning(f"未找到消息处理器: {message_type}")
                return None
        
        try:
            logger.debug(f"分发消息到处理器: {message_type}, 节点: {context.node_id}")
            return await handler.handle(payload, context)
        except Exception as e:
            logger.error(f"消息处理失败 - 类型: {message_type}, 节点: {context.node_id}, 错误: {e}")
            raise
    
    @property
    def registered_types(self) -> list[str]:
        """获取已注册的消息类型列表"""
        return list(self._handlers.keys())
    
    def has_handler(self, message_type: str) -> bool:
        """检查是否有指定类型的处理器"""
        return message_type in self._handlers


# 全局消息分发器实例
message_dispatcher = MessageDispatcher()
