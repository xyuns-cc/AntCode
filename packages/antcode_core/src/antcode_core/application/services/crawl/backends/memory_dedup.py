"""内存去重存储实现

基于 Python Set 实现的内存去重存储，支持容量限制和 LRU 淘汰。

Requirements: 2.3, 2.4, 2.5, 2.6, 2.7
"""

import asyncio
from collections import OrderedDict

from loguru import logger

from antcode_core.application.services.crawl.backends.dedup_backend import DedupStore


class InMemoryDedupStore(DedupStore):
    """内存去重存储实现

    基于 OrderedDict 实现，支持：
    - O(1) 的存在性检查和添加
    - 容量限制和 LRU 淘汰
    - 线程安全（通过 asyncio.Lock）

    Requirements: 2.3, 2.4, 2.5, 2.6, 2.7
    """

    # 默认配置
    DEFAULT_CAPACITY = 1000000  # 默认容量 100 万

    def __init__(self, max_capacity: int = None):
        """初始化内存去重存储

        Args:
            max_capacity: 最大容量，超过后使用 LRU 淘汰
        """
        self._max_capacity = max_capacity or self.DEFAULT_CAPACITY
        # 每个项目一个 OrderedDict，用于 LRU 淘汰
        self._stores: dict[str, OrderedDict[str, bool]] = {}
        # 每个项目的容量配置
        self._capacities: dict[str, int] = {}
        # 锁保护并发访问
        self._lock = asyncio.Lock()

    def _get_store(self, project_id: str) -> OrderedDict[str, bool]:
        """获取项目的去重存储"""
        if project_id not in self._stores:
            self._stores[project_id] = OrderedDict()
        return self._stores[project_id]

    def _get_capacity(self, project_id: str) -> int:
        """获取项目的容量限制"""
        return self._capacities.get(project_id, self._max_capacity)

    def _evict_if_needed(self, project_id: str, store: OrderedDict) -> int:
        """如果超过容量则淘汰旧元素

        Returns:
            淘汰的元素数量
        """
        capacity = self._get_capacity(project_id)
        evicted = 0

        while len(store) >= capacity:
            # 淘汰最旧的元素（OrderedDict 头部）
            store.popitem(last=False)
            evicted += 1

        if evicted > 0:
            logger.debug(f"去重存储 LRU 淘汰: project={project_id}, evicted={evicted}")

        return evicted

    async def exists(self, project_id: str, fingerprint: str) -> bool:
        """检查指纹是否存在

        Requirements: 2.4
        """
        async with self._lock:
            store = self._get_store(project_id)
            exists = fingerprint in store

            # 如果存在，移动到末尾（LRU）
            if exists:
                store.move_to_end(fingerprint)

            return exists

    async def add(self, project_id: str, fingerprint: str) -> bool:
        """添加指纹

        Returns:
            True 表示新添加成功，False 表示已存在

        Requirements: 2.5
        """
        async with self._lock:
            store = self._get_store(project_id)

            if fingerprint in store:
                # 已存在，移动到末尾
                store.move_to_end(fingerprint)
                return False

            # 检查容量，必要时淘汰
            self._evict_if_needed(project_id, store)

            # 添加新元素
            store[fingerprint] = True
            return True

    async def add_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量添加指纹

        Returns:
            布尔值列表，与输入列表一一对应

        Requirements: 2.6
        """
        if not fingerprints:
            return []

        results = []

        async with self._lock:
            store = self._get_store(project_id)

            for fingerprint in fingerprints:
                if fingerprint in store:
                    # 已存在
                    store.move_to_end(fingerprint)
                    results.append(False)
                else:
                    # 检查容量
                    self._evict_if_needed(project_id, store)
                    # 添加新元素
                    store[fingerprint] = True
                    results.append(True)

        return results

    async def exists_many(self, project_id: str, fingerprints: list[str]) -> list[bool]:
        """批量检查指纹是否存在"""
        if not fingerprints:
            return []

        results = []

        async with self._lock:
            store = self._get_store(project_id)

            for fingerprint in fingerprints:
                exists = fingerprint in store
                if exists:
                    store.move_to_end(fingerprint)
                results.append(exists)

        return results

    async def size(self, project_id: str) -> int:
        """获取去重集合大小

        Requirements: 2.7
        """
        async with self._lock:
            store = self._get_store(project_id)
            return len(store)

    async def clear(self, project_id: str) -> bool:
        """清空去重集合"""
        async with self._lock:
            if project_id in self._stores:
                self._stores[project_id].clear()
            return True

    async def ensure_store(
        self,
        project_id: str,
        capacity: int = 1000000,
        error_rate: float = 0.001,
    ) -> bool:
        """确保去重存储存在

        对于内存实现，主要是设置容量限制。
        error_rate 参数被忽略（仅 Bloom Filter 使用）。
        """
        async with self._lock:
            # 确保存储存在
            if project_id not in self._stores:
                self._stores[project_id] = OrderedDict()

            # 设置容量
            self._capacities[project_id] = capacity

            return True

    async def get_all_projects(self) -> list[str]:
        """获取所有项目 ID（用于测试）"""
        async with self._lock:
            return list(self._stores.keys())

    async def delete_store(self, project_id: str) -> bool:
        """删除项目的去重存储（用于测试）"""
        async with self._lock:
            if project_id in self._stores:
                del self._stores[project_id]
            if project_id in self._capacities:
                del self._capacities[project_id]
            return True
