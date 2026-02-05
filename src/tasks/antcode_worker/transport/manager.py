"""Worker 与 Master 的全局通信管理器实例"""

from .communication_manager import CommunicationManager

communication_manager = CommunicationManager()

__all__ = ["communication_manager"]

