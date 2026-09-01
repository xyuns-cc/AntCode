"""In-memory Redis fake for Crawl Redis preflight tests."""

from __future__ import annotations

import fnmatch
from typing import Any


class UpgradeRedisFake:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[Any, Any]] = {}
        self.sets: dict[str, set[Any]] = {}
        self.streams: dict[str, list[tuple[str, dict[Any, Any]]]] = {}
        self.zsets: dict[str, list[tuple[Any, float]]] = {}
        self.groups: dict[str, list[dict[Any, Any]]] = {}
        self.pending: dict[tuple[str, str], int] = {}
        self.explicit_types: dict[str, str] = {}

    def keys(self) -> set[str]:
        return {*self.hashes, *self.sets, *self.streams, *self.zsets, *self.explicit_types}

    async def scan_iter(self, *, match: str, count: int):
        del count
        for key in sorted(self.keys()):
            if fnmatch.fnmatchcase(key, match):
                yield key.encode()

    async def type(self, key: str) -> bytes:
        return self._type(key).encode()

    def _type(self, key: str) -> str:
        if key in self.explicit_types:
            return self.explicit_types[key]
        if key in self.hashes:
            return "hash"
        if key in self.sets:
            return "set"
        if key in self.streams:
            return "stream"
        if key in self.zsets:
            return "zset"
        return "none"

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def xlen(self, key: str) -> int:
        return len(self.streams.get(key, []))

    async def xinfo_groups(self, key: str) -> list[dict[Any, Any]]:
        return list(self.groups.get(key, []))

    async def xpending(self, key: str, group: str) -> dict[str, int]:
        return {"pending": self.pending.get((key, group), 0)}

    async def xrange(self, key: str, *, min: str, max: str, count: int):
        del max
        values = self.streams.get(key, [])
        if min.startswith("("):
            values = [item for item in values if _stream_id(item[0]) > _stream_id(min[1:])]
        return values[:count]

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, []))

    async def zscan_iter(self, key: str, *, count: int):
        del count
        for item in self.zsets.get(key, []):
            yield item

    async def hscan_iter(self, key: str, *, count: int):
        del count
        for item in self.hashes.get(key, {}).items():
            yield item


def _stream_id(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", 1)
    return int(milliseconds), int(sequence)
