"""插件基类。"""

from abc import ABC, abstractmethod

from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload


class PluginBase(ABC):
    """插件只负责匹配任务类型与生成 ExecPlan。

    插件**不得**直接执行进程、发起网络请求或上报日志与结果——那些由 executor 与
    transport 层负责，插件里做等于绕过沙箱与上报链路。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def priority(self) -> int:
        """越大越优先匹配。"""
        return 0

    @abstractmethod
    def match(self, payload: TaskPayload) -> bool:
        pass

    @abstractmethod
    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        pass

    def validate(self, payload: TaskPayload) -> list[str]:
        """返回错误列表，空表示验证通过。"""
        return []
