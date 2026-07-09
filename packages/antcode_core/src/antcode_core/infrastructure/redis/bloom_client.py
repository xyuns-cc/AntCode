"""RedisBloom client used by crawler deduplication."""

from dataclasses import dataclass

from antcode_core.infrastructure.redis.client import get_redis_client

MODULE_MISSING_ERRORS = ("unknown command", "err unknown")
KEY_MISSING_ERRORS = ("not found", "does not exist")


@dataclass(frozen=True)
class BloomFilterInfo:
    """RedisBloom filter metadata."""

    capacity: int = 0
    size: int = 0
    num_filters: int = 0
    num_items_inserted: int = 0
    expansion_rate: int = 0


def _error_text(exc: Exception) -> str:
    return str(exc).lower()


def _is_key_missing(exc: Exception) -> bool:
    error = _error_text(exc)
    return any(token in error for token in KEY_MISSING_ERRORS)


def _is_module_missing(exc: Exception) -> bool:
    error = _error_text(exc)
    return any(token in error for token in MODULE_MISSING_ERRORS)


class BloomFilterClient:
    """Strict RedisBloom wrapper.

    RedisBloom is required. Missing modules and command failures are raised
    directly so crawler deduplication cannot silently change semantics.
    """

    DEFAULT_CAPACITY = 1_000_000
    DEFAULT_ERROR_RATE = 0.001
    DEFAULT_EXPANSION = 2
    INFO_FIELD_PAIRS = 2

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._module_checked = False

    async def _get_client(self):
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis

    async def _ensure_module(self) -> None:
        if self._module_checked:
            return

        client = await self._get_client()
        try:
            await client.execute_command("BF.INFO", "__antcode_bloom_probe__")
        except Exception as exc:
            if _is_key_missing(exc):
                self._module_checked = True
                return
            if _is_module_missing(exc):
                raise RuntimeError("RedisBloom 模块不可用，必须安装 RedisBloom。") from exc
            raise
        self._module_checked = True

    async def bf_reserve(
        self,
        key: str,
        capacity: int | None = None,
        error_rate: float | None = None,
        expansion: int = DEFAULT_EXPANSION,
        nonscaling: bool = False,
    ) -> bool:
        await self._ensure_module()
        client = await self._get_client()
        args = [key, error_rate or self.DEFAULT_ERROR_RATE, capacity or self.DEFAULT_CAPACITY]
        if expansion != self.DEFAULT_EXPANSION:
            args.extend(["EXPANSION", expansion])
        if nonscaling:
            args.append("NONSCALING")

        try:
            await client.execute_command("BF.RESERVE", *args)
        except Exception as exc:
            if "item exists" in _error_text(exc):
                return True
            raise
        return True

    async def ensure_filter(
        self,
        key: str,
        capacity: int | None = None,
        error_rate: float | None = None,
    ) -> bool:
        await self._ensure_module()
        client = await self._get_client()
        if await client.exists(key):
            return True
        return await self.bf_reserve(key, capacity, error_rate)

    async def bf_add(self, key: str, item: str) -> bool:
        await self._ensure_module()
        client = await self._get_client()
        result = await client.execute_command("BF.ADD", key, item)
        return bool(result)

    async def bf_madd(self, key: str, items: list) -> list[bool]:
        if not items:
            return []
        await self._ensure_module()
        client = await self._get_client()
        result = await client.execute_command("BF.MADD", key, *items)
        return [bool(item) for item in result]

    async def bf_exists(self, key: str, item: str) -> bool:
        await self._ensure_module()
        client = await self._get_client()
        result = await client.execute_command("BF.EXISTS", key, item)
        return bool(result)

    async def bf_mexists(self, key: str, items: list) -> list[bool]:
        if not items:
            return []
        await self._ensure_module()
        client = await self._get_client()
        result = await client.execute_command("BF.MEXISTS", key, *items)
        return [bool(item) for item in result]

    async def add_if_not_exists(self, key: str, item: str) -> bool:
        return await self.bf_add(key, item)

    async def add_batch_if_not_exists(self, key: str, items: list) -> tuple[int, int]:
        if not items:
            return 0, 0
        results = await self.bf_madd(key, items)
        added = sum(1 for item in results if item)
        return added, len(results) - added

    async def filter_new_items(self, key: str, items: list) -> list:
        if not items:
            return []
        exists_results = await self.bf_mexists(key, items)
        return [item for item, exists in zip(items, exists_results, strict=False) if not exists]

    async def bf_info(self, key: str) -> BloomFilterInfo:
        await self._ensure_module()
        client = await self._get_client()
        try:
            result = await client.execute_command("BF.INFO", key)
        except Exception as exc:
            if _is_key_missing(exc):
                return BloomFilterInfo()
            raise
        return self._parse_info(result)

    def _parse_info(self, result: list) -> BloomFilterInfo:
        info = {}
        for index in range(0, len(result), self.INFO_FIELD_PAIRS):
            field = result[index]
            value = result[index + 1]
            if isinstance(field, bytes):
                field = field.decode("utf-8")
            info[str(field).lower()] = value

        return BloomFilterInfo(
            capacity=info.get("capacity", 0),
            size=info.get("size", 0),
            num_filters=info.get("number of filters", 0),
            num_items_inserted=info.get("number of items inserted", 0),
            expansion_rate=info.get("expansion rate", 0),
        )

    async def get_item_count(self, key: str) -> int:
        info = await self.bf_info(key)
        return info.num_items_inserted

    async def delete_filter(self, key: str) -> bool:
        client = await self._get_client()
        result = await client.delete(key)
        return bool(result)

    async def clear_filter(
        self,
        key: str,
        capacity: int | None = None,
        error_rate: float | None = None,
    ) -> bool:
        await self.delete_filter(key)
        return await self.bf_reserve(key, capacity, error_rate)
