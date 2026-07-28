"""migrate_lease_keys 单测共享的 Redis fake 与 client 注入助手。

被 test_migrate_lease_keys.py 与 test_migrate_lease_keys_recovery.py 共用。
"""

from __future__ import annotations

from scripts import migrate_lease_keys


class _Redis:
    def __init__(
        self,
        pages: list[tuple[int, list[str | bytes]]],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.pages = list(pages)
        self.close_error = close_error
        self.sources: dict[str, tuple[str, bytes | None, int]] = {}
        self.existing_targets: set[str] = set()
        self.deleted: list[str] = []
        self.restored: list[tuple[str, int, bytes, bool]] = []
        self.scan_calls: list[tuple[str, int]] = []
        self.closed = False
        self.hashes: dict[str, dict[str, bytes]] = {}
        self.zadds: list[tuple[str, dict[str, int]]] = []
        self.sadds: list[tuple[str, str]] = []

    def add_source(
        self,
        key: str,
        *,
        redis_type: str = "hash",
        payload: bytes | None = b"redis-dump",
        pttl_ms: int = 20_000,
    ) -> None:
        self.sources[key] = (redis_type, payload, pttl_ms)

    async def ping(self) -> bool:
        return True

    async def scan_iter(self, *, match: str, count: int):
        self.scan_calls.append((match, count))
        # P1-DR-03: 索引重建按目标 pattern（{ns}:lease:data:*）SCAN 派生，
        # 目标 Hash key 也要能被扫到；按 pattern 前缀区分新旧两类 key。
        if match.startswith("{"):
            prefix = match.removesuffix("*")
            for key in sorted(self.hashes):
                if key.startswith(prefix) and key not in self.deleted:
                    yield key
            return
        for _cursor, batch in self.pages:
            for key in batch:
                text = key.decode() if isinstance(key, bytes) else key
                # 已删除的 key 不应再被 SCAN 命中（索引清理前的复扫依赖此语义）
                if text in self.deleted:
                    continue
                yield key

    async def exists(self, key: str) -> bool:
        return key in self.existing_targets

    async def type(self, key: str) -> bytes:
        return self.sources.get(key, ("none", None, -2))[0].encode()

    async def dump(self, key: str) -> bytes | None:
        return self.sources.get(key, ("none", None, -2))[1]

    async def pttl(self, key: str) -> int:
        return self.sources.get(key, ("none", None, -2))[2]

    async def restore(self, key: str, ttl_ms: int, payload: bytes, *, replace: bool) -> None:
        self.restored.append((key, ttl_ms, payload, replace))
        self.existing_targets.add(key)

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        self.sources.pop(key, None)
        return 1

    async def hget(self, key: str, field: str) -> bytes | None:
        return self.hashes.get(key, {}).get(field)

    async def zadd(self, key: str, mapping: dict[str, int]) -> int:
        self.zadds.append((key, dict(mapping)))
        return len(mapping)

    async def sadd(self, key: str, member: str) -> int:
        self.sadds.append((key, member))
        return 1

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _install_client(monkeypatch, client: _Redis) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    def create(url: str, *, decode_responses: bool):
        calls.append((url, decode_responses))
        return client

    monkeypatch.setattr(migrate_lease_keys, "create_async_redis_client", create)
    return calls
