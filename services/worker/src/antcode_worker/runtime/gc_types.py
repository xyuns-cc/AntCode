"""运行时垃圾回收的结构化结果类型。"""

from typing import TypedDict


class CleanupResult(TypedDict):
    cleaned: int
    bytes_freed: int
    errors: list[str]


class CacheGCResult(TypedDict):
    envs_cleaned: int
    temp_cleaned: int
    bytes_freed: int
    errors: list[str]


class CacheStats(TypedDict):
    last_gc_time: str | None
    envs_cleaned: int
    temp_cleaned: int
    bytes_freed: int


class EnvInfo(TypedDict):
    name: str
    path: str
    last_used: float
    size: int


class GCRunResult(TypedDict):
    cleaned: int
    bytes_freed: int
    errors: list[str]
