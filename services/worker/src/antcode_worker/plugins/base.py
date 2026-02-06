"""
插件基类

Requirements: 8.2
"""

from abc import ABC, abstractmethod

from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload


class PluginBase(ABC):
    """
    插件基类

    插件只负责：
    1. 匹配任务类型
    2. 生成执行计划 (ExecPlan)

    插件不应该：
    - 直接执行进程
    - 进行网络请求
    - 上报日志或结果

    Requirements: 8.2
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass

    @property
    def priority(self) -> int:
        """插件优先级（越大越优先匹配）"""
        return 0

    @abstractmethod
    def match(self, payload: TaskPayload) -> bool:
        """
        判断是否匹配此任务

        Args:
            payload: 任务数据

        Returns:
            是否匹配
        """
        pass

    @abstractmethod
    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """
        构建执行计划

        Args:
            context: 执行上下文
            payload: 任务数据

        Returns:
            执行计划
        """
        pass

    def validate(self, payload: TaskPayload) -> list[str]:
        """
        验证任务数据

        Returns:
            错误列表，空表示验证通过
        """
        return []
