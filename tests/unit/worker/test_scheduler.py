"""
调度器测试

测试任务调度、优先级队列和背压机制。
"""

import asyncio
import sys
from pathlib import Path

import pytest

# 添加 worker 源码路径
worker_src = Path(__file__).parent.parent.parent.parent / "services" / "worker" / "src"
if str(worker_src) not in sys.path:
    sys.path.insert(0, str(worker_src))

from antcode_worker.engine.scheduler import QueueItem, Scheduler


class TestQueueItem:
    """队列项测试"""

    def test_priority_ordering(self):
        """测试优先级排序"""
        item1 = QueueItem(
            priority=-10,  # 高优先级
            enqueue_time=100.0,
            run_id="task1",
            data={},
        )
        item2 = QueueItem(
            priority=-1,  # 低优先级
            enqueue_time=100.0,
            run_id="task2",
            data={},
        )

        # 优先级数字越小排在前面（-10 < -1）
        assert item1 < item2

    def test_same_priority_uses_enqueue_time(self):
        """测试相同优先级使用入队时间排序"""
        item1 = QueueItem(
            priority=-5,
            enqueue_time=100.0,
            run_id="task1",
            data={},
        )
        item2 = QueueItem(
            priority=-5,
            enqueue_time=200.0,
            run_id="task2",
            data={},
        )

        # 先入队的排在前面
        assert item1 < item2


class TestScheduler:
    """调度器测试"""

    @pytest.fixture
    def scheduler(self):
        """创建调度器实例"""
        return Scheduler(max_queue_size=10)

    @pytest.mark.asyncio
    async def test_start_stop(self, scheduler):
        """测试启动和停止"""
        await scheduler.start()
        assert scheduler._running

        await scheduler.stop()
        assert not scheduler._running

    @pytest.mark.asyncio
    async def test_enqueue_success(self, scheduler):
        """测试入队成功"""
        await scheduler.start()

        result = await scheduler.enqueue(
            run_id="task1",
            data={"key": "value"},
            priority=1,
        )

        assert result is True
        assert scheduler.size == 1

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_enqueue_full_blocks(self, scheduler):
        """测试队列满时阻塞"""
        scheduler = Scheduler(max_queue_size=2)
        await scheduler.start()

        await scheduler.enqueue(run_id="task1", data={})
        await scheduler.enqueue(run_id="task2", data={})

        # 第三个任务应该超时
        result = await scheduler.enqueue(run_id="task3", data={}, timeout=0.1)
        assert result is False

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_dequeue_priority_order(self, scheduler):
        """测试按优先级出队"""
        await scheduler.start()

        # 入队不同优先级的任务
        await scheduler.enqueue(run_id="low", data={}, priority=1)
        await scheduler.enqueue(run_id="high", data={}, priority=10)
        await scheduler.enqueue(run_id="mid", data={}, priority=5)

        # 按优先级顺序出队
        result1 = await scheduler.dequeue(timeout=0.1)
        result2 = await scheduler.dequeue(timeout=0.1)
        result3 = await scheduler.dequeue(timeout=0.1)

        assert result1[0] == "high"
        assert result2[0] == "mid"
        assert result3[0] == "low"

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_dequeue_timeout(self, scheduler):
        """测试出队超时"""
        await scheduler.start()

        # 空队列应该超时返回 None
        result = await scheduler.dequeue(timeout=0.1)
        assert result is None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove(self, scheduler):
        """测试移除任务"""
        await scheduler.start()

        await scheduler.enqueue(run_id="task1", data={}, priority=1)
        result = await scheduler.remove("task1")

        assert result is True
        assert scheduler.size == 0

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, scheduler):
        """测试移除不存在的任务"""
        await scheduler.start()

        result = await scheduler.remove("nonexistent")
        assert result is False

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_removed_task_skipped_on_dequeue(self, scheduler):
        """测试已移除的任务在出队时被跳过"""
        await scheduler.start()

        await scheduler.enqueue(run_id="task1", data={}, priority=10)
        await scheduler.enqueue(run_id="task2", data={}, priority=5)

        # 移除第一个任务
        await scheduler.remove("task1")

        # 出队应该跳过被移除的任务
        result = await scheduler.dequeue(timeout=0.1)
        assert result[0] == "task2"

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_size_property(self, scheduler):
        """测试 size 属性"""
        await scheduler.start()

        assert scheduler.size == 0
        assert scheduler.is_empty is True

        await scheduler.enqueue(run_id="task1", data={})
        assert scheduler.size == 1
        assert scheduler.is_empty is False

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_is_full_property(self, scheduler):
        """测试 is_full 属性"""
        scheduler = Scheduler(max_queue_size=2)
        await scheduler.start()

        assert scheduler.is_full is False

        await scheduler.enqueue(run_id="task1", data={})
        await scheduler.enqueue(run_id="task2", data={})

        assert scheduler.is_full is True

        await scheduler.stop()
