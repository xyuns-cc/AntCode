"""执行器层

负责具体的任务执行：
- 基础执行器
- 代码执行器
- 爬虫执行器
- 安全执行器 (带资源限制和安全扫描)
- 渲染执行器 (DrissionPage 浏览器抓取)
- 执行器工厂
"""

from .base import (
    BaseExecutor,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
)
from ..utils.exceptions import SecurityError

from .code_executor import CodeExecutor
from .spider_executor import SpiderExecutor
from .secure_executor import SecureTaskExecutor, ResourceLimits
from .render_executor import RenderExecutor


class ExecutorFactory:
    """执行器工厂
    
    根据任务类型创建对应的执行器实例
    """

    @staticmethod
    def create(
        executor_type: str = "code",
        signals=None,
        max_concurrent: int = 1,
        cpu_limit: int = None,
        memory_limit: int = None,
        enable_security_scan: bool = False,
        render_config=None,
        **kwargs
    ) -> BaseExecutor:
        """创建执行器
        
        Args:
            executor_type: 执行器类型 (code/secure/spider/render)
            signals: 信号管理器
            max_concurrent: 最大并发数
            cpu_limit: CPU 时间限制
            memory_limit: 内存限制 (MB)
            enable_security_scan: 是否启用安全扫描
            render_config: 渲染配置 (RenderConfig)
        
        Returns:
            对应类型的执行器实例
        """
        if executor_type == "secure":
            return SecureTaskExecutor(
                signals=signals,
                max_concurrent=max_concurrent,
                enable_security_scan=enable_security_scan,
                **kwargs
            )
        elif executor_type == "spider":
            return SpiderExecutor(
                signals=signals,
                max_concurrent=max_concurrent,
                **kwargs
            )
        elif executor_type == "render":
            return RenderExecutor(
                signals=signals,
                max_concurrent=max_concurrent,
                render_config=render_config,
                **kwargs
            )
        else:
            # 默认使用 CodeExecutor
            return CodeExecutor(
                signals=signals,
                max_concurrent=max_concurrent,
                cpu_limit=cpu_limit,
                memory_limit=memory_limit,
                **kwargs
            )


__all__ = [
    "BaseExecutor",
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionStatus",
    "CodeExecutor",
    "SpiderExecutor",
    "SecureTaskExecutor",
    "ResourceLimits",
    "SecurityError",
    "RenderExecutor",
    "ExecutorFactory",
]
