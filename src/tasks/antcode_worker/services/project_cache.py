"""
项目缓存服务
负责 Worker 端项目文件的本地缓存管理
"""
import asyncio
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any

from loguru import logger

from ..utils.serialization import Serializer


@dataclass
class ProjectCacheEntry:
    """项目缓存条目"""
    project_id: str
    file_hash: str
    file_path: str
    file_size: int
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    access_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectCacheEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ProjectCache:
    """
    项目缓存 - Worker 端项目文件本地缓存
    
    功能：
    - 基于 file_hash 的缓存键
    - LRU 淘汰策略
    - 缓存索引持久化
    """

    INDEX_FILE = ".project_cache_index.json"

    def __init__(
        self,
        cache_dir: str,
        max_size: int = 100,
        ttl_hours: int = 168,
    ):
        """
        初始化项目缓存
        
        Args:
            cache_dir: 缓存目录路径
            max_size: 最大缓存项目数
            ttl_hours: 缓存最大保留时间（小时）
        """
        self.cache_dir = cache_dir
        self.max_size = max_size
        self.ttl_hours = ttl_hours

        # 缓存索引: {cache_key: ProjectCacheEntry}
        # cache_key = f"{project_id}:{file_hash}"
        self._entries: Dict[str, ProjectCacheEntry] = {}
        self._lock = asyncio.Lock()

        # 统计信息
        self._hits = 0
        self._misses = 0
        self._evictions = 0

        # 确保缓存目录存在
        os.makedirs(cache_dir, exist_ok=True)

    @property
    def _index_path(self) -> str:
        """缓存索引文件路径"""
        return os.path.join(self.cache_dir, self.INDEX_FILE)

    def _make_cache_key(self, project_id: str, file_hash: str) -> str:
        """生成缓存键"""
        return f"{project_id}:{file_hash}"

    def get(self, project_id: str, file_hash: str) -> Optional[str]:
        """
        获取缓存的项目路径
        
        Args:
            project_id: 项目ID
            file_hash: 文件哈希
            
        Returns:
            缓存的文件路径，未命中返回 None
        """
        cache_key = self._make_cache_key(project_id, file_hash)
        entry = self._entries.get(cache_key)

        if entry is None:
            self._misses += 1
            return None

        # 验证文件是否存在
        if not os.path.exists(entry.file_path):
            logger.warning(f"缓存文件不存在: {entry.file_path}")
            del self._entries[cache_key]
            self._misses += 1
            return None

        # 检查 TTL
        now = time.time()
        age_hours = (now - entry.created_at) / 3600
        if age_hours > self.ttl_hours:
            logger.info(f"缓存已过期: {project_id} (age={age_hours:.1f}h)")
            del self._entries[cache_key]
            self._misses += 1
            return None

        # 更新访问信息
        entry.last_access = now
        entry.access_count += 1
        self._hits += 1

        logger.debug(f"缓存命中: {project_id} (hash={file_hash[:8]}...)")
        return entry.file_path

    async def put(
        self,
        project_id: str,
        file_hash: str,
        file_path: str,
    ) -> None:
        """
        添加项目到缓存
        
        Args:
            project_id: 项目ID
            file_hash: 文件哈希
            file_path: 文件路径
        """
        async with self._lock:
            # 先检查是否需要淘汰
            await self._evict_if_needed_locked()

            cache_key = self._make_cache_key(project_id, file_hash)

            # 获取文件大小
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                file_size = 0

            now = time.time()
            entry = ProjectCacheEntry(
                project_id=project_id,
                file_hash=file_hash,
                file_path=file_path,
                file_size=file_size,
                created_at=now,
                last_access=now,
                access_count=1,
            )

            self._entries[cache_key] = entry
            logger.debug(f"添加缓存: {project_id} (hash={file_hash[:8]}...)")

    def contains(self, project_id: str, file_hash: str) -> bool:
        """
        检查缓存是否包含指定项目
        
        Args:
            project_id: 项目ID
            file_hash: 文件哈希
            
        Returns:
            是否存在于缓存中
        """
        cache_key = self._make_cache_key(project_id, file_hash)
        entry = self._entries.get(cache_key)

        if entry is None:
            return False

        # 验证文件存在且未过期
        if not os.path.exists(entry.file_path):
            return False

        age_hours = (time.time() - entry.created_at) / 3600
        if age_hours > self.ttl_hours:
            return False

        return True

    async def evict_if_needed(self) -> int:
        """
        按需淘汰缓存（LRU 策略）
        
        Returns:
            淘汰的项目数量
        """
        async with self._lock:
            return await self._evict_if_needed_locked()

    async def _evict_if_needed_locked(self) -> int:
        """内部淘汰方法（需要持有锁）"""
        if len(self._entries) < self.max_size:
            return 0

        # 按 last_access 排序，淘汰最久未访问的
        sorted_entries = sorted(
            self._entries.items(),
            key=lambda x: x[1].last_access,
        )

        # 淘汰到 max_size - 1，为新项目腾出空间
        evict_count = len(self._entries) - self.max_size + 1
        evicted = 0

        for cache_key, entry in sorted_entries[:evict_count]:
            self._evict_entry(cache_key, entry)
            evicted += 1

        if evicted > 0:
            logger.info(f"LRU 淘汰: {evicted} 个项目")

        return evicted

    def _evict_entry(self, cache_key: str, entry: ProjectCacheEntry) -> None:
        """淘汰单个缓存条目"""
        # 从索引中移除
        self._entries.pop(cache_key, None)
        self._evictions += 1

        # 可选：删除文件（节省磁盘空间）
        # 这里不删除文件，因为可能被其他地方引用
        logger.debug(f"淘汰缓存: {entry.project_id}")

    async def load_index(self) -> None:
        """从磁盘加载缓存索引（使用 ujson 提升读取性能）"""
        if not os.path.exists(self._index_path):
            logger.info("缓存索引文件不存在，使用空缓存")
            return

        try:
            async with self._lock:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    data = Serializer.from_json(content)

                loaded = 0
                for cache_key, entry_data in data.get("entries", {}).items():
                    entry = ProjectCacheEntry.from_dict(entry_data)

                    # 验证文件存在
                    if os.path.exists(entry.file_path):
                        self._entries[cache_key] = entry
                        loaded += 1
                    else:
                        logger.debug(f"跳过不存在的缓存: {entry.project_id}")

                logger.info(f"加载缓存索引: {loaded} 个项目")
        except Exception as e:
            logger.error(f"加载缓存索引失败: {e}")

    async def save_index(self) -> None:
        """保存缓存索引到磁盘（使用 ujson 提升写入性能）"""
        try:
            async with self._lock:
                data = {
                    "entries": {
                        key: entry.to_dict()
                        for key, entry in self._entries.items()
                    },
                    "stats": {
                        "hits": self._hits,
                        "misses": self._misses,
                        "evictions": self._evictions,
                    },
                    "saved_at": time.time(),
                }

                # 使用 ujson 序列化，然后手动格式化写入
                # 注意：ujson 不支持 indent 参数，但性能更好
                json_content = Serializer.to_json(data)
                with open(self._index_path, "w", encoding="utf-8") as f:
                    f.write(json_content)

                logger.info(f"保存缓存索引: {len(self._entries)} 个项目")
        except Exception as e:
            logger.error(f"保存缓存索引失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

        total_size = sum(e.file_size for e in self._entries.values())

        return {
            "cached_count": len(self._entries),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "evictions": self._evictions,
            "total_size_bytes": total_size,
            "ttl_hours": self.ttl_hours,
        }

    def clear(self) -> None:
        """清空缓存"""
        self._entries.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        logger.info("缓存已清空")
