"""
执行节点解析器 - 根据执行策略确定任务执行节点

支持的执行策略：
- local: 本地执行（主节点）
- fixed: 固定节点（仅在绑定节点执行，不可用时失败）
- specified: 指定节点（任务级别指定）
- auto: 自动选择（负载均衡）
- prefer: 优先绑定节点（不可用时自动选择其他节点）
"""

from typing import Optional, Tuple
from loguru import logger

from src.core.exceptions import NodeUnavailableError
from src.models import Node, Project, NodeStatus
from src.models.scheduler import ScheduledTask
from src.models.enums import ExecutionStrategy


class ExecutionResolver:
    """执行节点解析器"""

    async def resolve_execution_node(
        self,
        task: ScheduledTask,
        project: Project,
    ) -> Tuple[Optional[Node], str]:
        """
        解析任务应该在哪个节点执行
        
        Args:
            task: 调度任务
            project: 关联的项目
            
        Returns:
            (node, strategy): 执行节点（None表示本地执行）和实际使用的策略
            
        Raises:
            NodeUnavailableError: 当指定/绑定的节点不可用且无法故障转移时
        """
        # 确定实际使用的执行策略（任务级别覆盖项目级别）
        strategy = self._get_effective_strategy(task, project)

        logger.debug(f"任务 {task.name} 使用执行策略: {strategy}")

        if strategy == ExecutionStrategy.LOCAL:
            return None, strategy.value

        elif strategy == ExecutionStrategy.FIXED_NODE:
            return await self._resolve_fixed_node(project), strategy.value

        elif strategy == ExecutionStrategy.SPECIFIED:
            return await self._resolve_specified_node(task), strategy.value

        elif strategy == ExecutionStrategy.AUTO_SELECT:
            return await self._resolve_auto_select(project), strategy.value

        elif strategy == ExecutionStrategy.PREFER_BOUND:
            return await self._resolve_prefer_bound(project), strategy.value

        else:
            logger.warning(f"未知的执行策略: {strategy}，使用本地执行")
            return None, "local"

    def _get_effective_strategy(
        self,
        task: ScheduledTask,
        project: Project,
    ) -> ExecutionStrategy:
        """获取实际使用的执行策略"""
        # 任务级别策略优先
        if task.execution_strategy:
            return task.execution_strategy

        # 项目级别策略
        if project.execution_strategy:
            return project.execution_strategy

        # 兼容旧数据：如果任务有 node_id 或 specified_node_id，使用 specified 策略
        if task.specified_node_id or task.node_id:
            return ExecutionStrategy.SPECIFIED

        # 兼容旧数据：如果项目有 bound_node_id，使用 prefer 策略
        if project.bound_node_id:
            return ExecutionStrategy.PREFER_BOUND

        # 默认：本地执行
        return ExecutionStrategy.LOCAL

    async def _resolve_fixed_node(self, project: Project) -> Node:
        """解析固定节点策略"""
        if not project.bound_node_id:
            raise NodeUnavailableError("项目未绑定执行节点")

        node = await Node.get_or_none(id=project.bound_node_id)
        if not node:
            raise NodeUnavailableError(
                f"绑定的节点不存在 (id={project.bound_node_id})",
                project.bound_node_id
            )

        if node.status != NodeStatus.ONLINE:
            raise NodeUnavailableError(
                f"绑定的节点 [{node.name}] 不在线 (状态: {node.status})",
                node.id
            )

        logger.info(f"固定节点策略：使用绑定节点 [{node.name}]")
        return node

    async def _resolve_specified_node(self, task: ScheduledTask) -> Node:
        """解析指定节点策略"""
        # 优先使用新字段，兼容旧字段
        node_id = task.specified_node_id or task.node_id

        if not node_id:
            raise NodeUnavailableError("任务未指定执行节点")

        node = await Node.get_or_none(id=node_id)
        if not node:
            raise NodeUnavailableError(
                f"指定的节点不存在 (id={node_id})",
                node_id
            )

        if node.status != NodeStatus.ONLINE:
            raise NodeUnavailableError(
                f"指定的节点 [{node.name}] 不在线 (状态: {node.status})",
                node.id
            )

        logger.info(f"指定节点策略：使用节点 [{node.name}]")
        return node

    async def _resolve_auto_select(
        self,
        project: Project,
        exclude_nodes: Optional[list] = None,
    ) -> Optional[Node]:
        """解析自动选择策略"""
        from src.services.nodes import node_load_balancer

        # 检查项目是否需要渲染能力
        require_render = await self._check_render_requirement(project)

        best_node = await node_load_balancer.select_best_node(
            exclude_nodes=exclude_nodes,
            require_render=require_render,
        )

        if not best_node:
            logger.warning("自动选择策略：无可用节点，将使用本地执行")
            return None

        logger.info(f"自动选择策略：选中节点 [{best_node.name}]")
        return best_node

    async def _resolve_prefer_bound(self, project: Project) -> Optional[Node]:
        """解析优先绑定节点策略"""
        # 1. 尝试使用绑定节点
        if project.bound_node_id:
            node = await Node.get_or_none(id=project.bound_node_id)
            if node and node.status == NodeStatus.ONLINE:
                logger.info(f"优先绑定策略：使用绑定节点 [{node.name}]")
                return node

            # 绑定节点不可用
            if node:
                logger.warning(f"绑定节点 [{node.name}] 不在线 (状态: {node.status})")
            else:
                logger.warning(f"绑定节点不存在 (id={project.bound_node_id})")

        # 2. 检查是否启用故障转移
        if not project.fallback_enabled:
            if project.bound_node_id:
                raise NodeUnavailableError(
                    "绑定节点不可用且未启用故障转移",
                    project.bound_node_id
                )
            # 没有绑定节点，使用本地执行
            logger.info("优先绑定策略：未绑定节点，使用本地执行")
            return None

        # 3. 故障转移：自动选择其他节点
        logger.info("优先绑定策略：启用故障转移，自动选择其他节点")
        exclude_nodes = [project.bound_node_id] if project.bound_node_id else None

        return await self._resolve_auto_select(project, exclude_nodes)

    async def _check_render_requirement(self, project: Project) -> bool:
        """检查项目是否需要渲染能力"""
        from src.models.project import ProjectRule
        from src.models.enums import CrawlEngine

        # 规则项目：检查是否使用浏览器引擎
        if project.type.value == "rule":
            rule = await ProjectRule.get_or_none(project_id=project.id)
            if rule and rule.engine == CrawlEngine.BROWSER:
                return True

        return False


# 全局实例
execution_resolver = ExecutionResolver()
