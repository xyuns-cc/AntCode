"""URL 去重服务

基于抽象后端实现 URL 去重，支持 Redis Bloom Filter 和内存 Set 两种实现。

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
"""

from loguru import logger

from antcode_core.common.hash_utils import calculate_content_hash
from antcode_core.application.services.base import BaseService
from antcode_core.application.services.crawl.backends.dedup_backend import DedupStore, get_dedup_store


def calculate_url_fingerprint(url: str) -> str:
    """计算 URL 指纹

    使用 MD5 哈希算法计算 URL 的指纹，用于去重检查。
    对于相同的 URL，多次计算应得到相同的指纹。

    Args:
        url: URL 字符串

    Returns:
        URL 的 MD5 哈希指纹（32 位十六进制字符串）
    """
    # 规范化 URL：去除首尾空白
    normalized_url = url.strip()
    return calculate_content_hash(normalized_url, algorithm="md5")


class CrawlDedupService(BaseService):
    """URL 去重服务

    基于抽象后端实现高效的 URL 去重，支持：
    - 单个 URL 的存在性检查
    - 单个 URL 的添加
    - 批量 URL 的检查和添加
    - 自动计算 URL 指纹

    通过环境变量 CRAWL_BACKEND 配置后端类型：
    - "memory": 内存 Set 实现（默认）
    - "redis": Redis Bloom Filter 实现

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8
    """

    # 默认配置
    DEFAULT_CAPACITY = 1000000  # 默认容量 100 万
    DEFAULT_ERROR_RATE = 0.001  # 默认误判率 0.1%

    def __init__(self, dedup_store: DedupStore = None):
        """初始化去重服务

        Args:
            dedup_store: 去重存储后端，为 None 时通过工厂方法获取
        """
        super().__init__()
        self._dedup_store = dedup_store

    @property
    def dedup_store(self) -> DedupStore:
        """获取去重存储后端（延迟初始化）"""
        if self._dedup_store is None:
            self._dedup_store = get_dedup_store()
        return self._dedup_store

    async def ensure_filter(
        self,
        project_id: str,
        capacity: int = None,
        error_rate: float = None,
    ) -> bool:
        """确保项目的去重存储存在

        Args:
            project_id: 项目 ID
            capacity: 预期容量，默认 100 万
            error_rate: 误判率，默认 0.1%

        Returns:
            是否成功
        """
        capacity = capacity or self.DEFAULT_CAPACITY
        error_rate = error_rate or self.DEFAULT_ERROR_RATE

        return await self.dedup_store.ensure_store(project_id, capacity, error_rate)

    async def exists(self, project_id: str, url: str) -> bool:
        """检查 URL 是否已存在

        Args:
            project_id: 项目 ID
            url: 要检查的 URL

        Returns:
            True 表示可能存在，False 表示一定不存在
        """
        fingerprint = calculate_url_fingerprint(url)
        result = await self.dedup_store.exists(project_id, fingerprint)

        logger.debug(
            f"检查 URL 去重: project={project_id}, url={url[:50]}..., "
            f"fingerprint={fingerprint[:8]}..., exists={result}"
        )

        return result

    async def add(self, project_id: str, url: str) -> bool:
        """添加 URL 到去重存储

        Args:
            project_id: 项目 ID
            url: 要添加的 URL

        Returns:
            True 表示新添加成功，False 表示已存在
        """
        fingerprint = calculate_url_fingerprint(url)
        result = await self.dedup_store.add(project_id, fingerprint)

        if result:
            logger.debug(
                f"添加 URL 到去重存储: project={project_id}, "
                f"url={url[:50]}..., fingerprint={fingerprint[:8]}..."
            )
        else:
            logger.debug(
                f"URL 已存在于去重存储: project={project_id}, "
                f"url={url[:50]}..., fingerprint={fingerprint[:8]}..."
            )

        return result

    async def add_if_not_exists(self, project_id: str, url: str) -> bool:
        """如果 URL 不存在则添加

        这是一个原子操作，等同于 add() 方法。

        Args:
            project_id: 项目 ID
            url: 要添加的 URL

        Returns:
            True 表示新添加成功（之前不存在），False 表示已存在
        """
        return await self.add(project_id, url)

    async def exists_batch(self, project_id: str, urls: list) -> list:
        """批量检查 URL 是否存在

        Args:
            project_id: 项目 ID
            urls: 要检查的 URL 列表

        Returns:
            布尔值列表，与输入 URL 列表一一对应
        """
        if not urls:
            return []

        fingerprints = [calculate_url_fingerprint(url) for url in urls]
        results = await self.dedup_store.exists_many(project_id, fingerprints)

        logger.debug(
            f"批量检查 URL 去重: project={project_id}, "
            f"count={len(urls)}, exists_count={sum(results)}"
        )

        return results

    async def add_batch(self, project_id: str, urls: list) -> tuple:
        """批量添加 URL 到去重存储

        Args:
            project_id: 项目 ID
            urls: 要添加的 URL 列表

        Returns:
            (added_count, duplicate_count) 元组
        """
        if not urls:
            return 0, 0

        fingerprints = [calculate_url_fingerprint(url) for url in urls]
        results = await self.dedup_store.add_many(project_id, fingerprints)

        added = sum(1 for r in results if r)
        duplicate = len(results) - added

        logger.info(
            f"批量添加 URL 到去重存储: project={project_id}, "
            f"total={len(urls)}, added={added}, duplicate={duplicate}"
        )

        return added, duplicate

    async def filter_new_urls(self, project_id: str, urls: list) -> list:
        """过滤出不存在的 URL

        Args:
            project_id: 项目 ID
            urls: 要检查的 URL 列表

        Returns:
            不存在的 URL 列表
        """
        if not urls:
            return []

        exists_results = await self.exists_batch(project_id, urls)

        new_urls = []
        for url, exists in zip(urls, exists_results, strict=False):
            if not exists:
                new_urls.append(url)

        logger.debug(
            f"过滤新 URL: project={project_id}, "
            f"input={len(urls)}, new={len(new_urls)}"
        )

        return new_urls

    async def filter_and_add_new_urls(self, project_id: str, urls: list) -> tuple:
        """过滤出不存在的 URL 并添加到存储

        Args:
            project_id: 项目 ID
            urls: 要处理的 URL 列表

        Returns:
            (new_urls, added_count, duplicate_count) 元组
        """
        if not urls:
            return [], 0, 0

        fingerprints = [calculate_url_fingerprint(url) for url in urls]
        results = await self.dedup_store.add_many(project_id, fingerprints)

        new_urls = []
        added = 0
        duplicate = 0

        for url, is_new in zip(urls, results, strict=False):
            if is_new:
                new_urls.append(url)
                added += 1
            else:
                duplicate += 1

        return new_urls, added, duplicate

    async def get_url_count(self, project_id: str) -> int:
        """获取已添加的 URL 数量

        Args:
            project_id: 项目 ID

        Returns:
            URL 数量
        """
        return await self.dedup_store.size(project_id)

    async def clear_filter(
        self,
        project_id: str,
        capacity: int = None,
        error_rate: float = None,
    ) -> bool:
        """清空并重建去重存储

        Args:
            project_id: 项目 ID
            capacity: 新容量
            error_rate: 新误判率

        Returns:
            是否成功
        """
        # 先清空
        await self.dedup_store.clear(project_id)

        # 重新确保存储存在
        capacity = capacity or self.DEFAULT_CAPACITY
        error_rate = error_rate or self.DEFAULT_ERROR_RATE

        result = await self.dedup_store.ensure_store(project_id, capacity, error_rate)

        logger.info(
            f"清空去重存储: project={project_id}, "
            f"capacity={capacity}, error_rate={error_rate}"
        )

        return result

    async def delete_filter(self, project_id: str) -> bool:
        """删除去重存储

        Args:
            project_id: 项目 ID

        Returns:
            是否删除成功
        """
        result = await self.dedup_store.clear(project_id)

        logger.info(f"删除去重存储: project={project_id}")

        return result

    async def clear(self, project_id: str) -> bool:
        """清除去重存储（delete_filter 的别名）"""
        return await self.delete_filter(project_id)

    async def get_size(self, project_id: str) -> int:
        """获取去重集合大小（get_url_count 的别名）"""
        return await self.get_url_count(project_id)

    async def get_filter_info(self, project_id: str) -> dict:
        """获取去重存储信息

        Args:
            project_id: 项目 ID

        Returns:
            存储信息字典
        """
        # 检查后端是否支持 get_filter_info
        if hasattr(self.dedup_store, 'get_filter_info'):
            return await self.dedup_store.get_filter_info(project_id)

        # 内存后端返回基本信息
        size = await self.dedup_store.size(project_id)
        return {
            "project_id": project_id,
            "num_items": size,
        }


# 全局服务实例
crawl_dedup_service = CrawlDedupService()
