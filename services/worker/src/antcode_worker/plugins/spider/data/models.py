"""
爬虫数据模型

定义爬虫数据存储相关的数据结构：
- SpiderDataItem: 爬虫数据条目
- SpiderMeta: 爬虫运行元数据
- SpiderConfig: 爬虫动态配置
"""

from __future__ import annotations

import contextlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SpiderDataItem:
    """
    爬虫数据条目

    存储单条爬取的数据，支持 JSON 序列化存入 Redis。
    """

    run_id: str
    project_id: str
    spider_name: str
    data: dict[str, Any]
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    url: str = ""
    item_type: str = "default"
    timestamp: datetime = field(default_factory=datetime.now)
    sequence: int = 0

    def to_redis_dict(self) -> dict[str, str]:
        """转换为 Redis Hash/Stream 存储格式"""
        return {
            "item_id": self.item_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "spider_name": self.spider_name,
            "item_type": self.item_type,
            "data": json.dumps(self.data, ensure_ascii=False),
            "url": self.url,
            "timestamp": self.timestamp.isoformat(),
            "sequence": str(self.sequence),
        }

    @classmethod
    def from_redis_dict(cls, data: dict[str, str]) -> SpiderDataItem:
        """从 Redis 数据恢复"""
        return cls(
            item_id=data.get("item_id", ""),
            run_id=data.get("run_id", ""),
            project_id=data.get("project_id", ""),
            spider_name=data.get("spider_name", ""),
            item_type=data.get("item_type", "default"),
            data=json.loads(data.get("data", "{}")),
            url=data.get("url", ""),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else datetime.now(),
            sequence=int(data.get("sequence", 0)),
        )

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_redis_dict(), ensure_ascii=False)


@dataclass
class SpiderMeta:
    """
    爬虫运行元数据

    记录爬虫运行的状态和统计信息。
    """

    run_id: str
    project_id: str
    spider_name: str
    status: str = "running"  # running, completed, failed, cancelled
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items_count: int = 0
    pages_count: int = 0
    errors_count: int = 0
    duration_ms: float = 0
    config: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_redis_dict(self) -> dict[str, str]:
        """转换为 Redis Hash 存储格式"""
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "spider_name": self.spider_name,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "finished_at": self.finished_at.isoformat() if self.finished_at else "",
            "items_count": str(self.items_count),
            "pages_count": str(self.pages_count),
            "errors_count": str(self.errors_count),
            "duration_ms": str(self.duration_ms),
            "config": json.dumps(self.config, ensure_ascii=False),
            "errors": json.dumps(self.errors, ensure_ascii=False),
        }

    @classmethod
    def from_redis_dict(cls, data: dict[str, str]) -> SpiderMeta:
        """从 Redis 数据恢复"""
        started_at = None
        if data.get("started_at"):
            started_at = datetime.fromisoformat(data["started_at"])

        finished_at = None
        if data.get("finished_at"):
            finished_at = datetime.fromisoformat(data["finished_at"])

        config = {}
        if data.get("config"):
            with contextlib.suppress(json.JSONDecodeError):
                config = json.loads(data["config"])

        errors = []
        if data.get("errors"):
            with contextlib.suppress(json.JSONDecodeError):
                errors = json.loads(data["errors"])

        return cls(
            run_id=data.get("run_id", ""),
            project_id=data.get("project_id", ""),
            spider_name=data.get("spider_name", ""),
            status=data.get("status", "running"),
            started_at=started_at,
            finished_at=finished_at,
            items_count=int(data.get("items_count", 0)),
            pages_count=int(data.get("pages_count", 0)),
            errors_count=int(data.get("errors_count", 0)),
            duration_ms=float(data.get("duration_ms", 0)),
            config=config,
            errors=errors,
        )


@dataclass
class SpiderConfig:
    """
    爬虫动态配置

    支持运行时配置爬虫行为，便于后期数据处理。
    """

    project_id: str
    item_schema: dict[str, Any] | None = None  # JSON Schema 验证
    dedup_fields: list[str] = field(default_factory=list)  # 去重字段
    ttl_seconds: int = 0  # 0 = 不自动过期
    max_items: int = 0  # 0 = 不裁剪
    post_processors: list[str] = field(default_factory=list)  # 后处理器
    custom_settings: dict[str, Any] = field(default_factory=dict)

    def to_redis_dict(self) -> dict[str, str]:
        """转换为 Redis Hash 存储格式"""
        return {
            "project_id": self.project_id,
            "item_schema": json.dumps(self.item_schema, ensure_ascii=False)
            if self.item_schema
            else "",
            "dedup_fields": json.dumps(self.dedup_fields, ensure_ascii=False),
            "ttl_seconds": str(self.ttl_seconds),
            "max_items": str(self.max_items),
            "post_processors": json.dumps(self.post_processors, ensure_ascii=False),
            "custom_settings": json.dumps(self.custom_settings, ensure_ascii=False),
        }

    @classmethod
    def from_redis_dict(cls, data: dict[str, str]) -> SpiderConfig:
        """从 Redis 数据恢复"""
        item_schema = None
        if data.get("item_schema"):
            with contextlib.suppress(json.JSONDecodeError):
                item_schema = json.loads(data["item_schema"])

        dedup_fields = []
        if data.get("dedup_fields"):
            with contextlib.suppress(json.JSONDecodeError):
                dedup_fields = json.loads(data["dedup_fields"])

        post_processors = []
        if data.get("post_processors"):
            with contextlib.suppress(json.JSONDecodeError):
                post_processors = json.loads(data["post_processors"])

        custom_settings = {}
        if data.get("custom_settings"):
            with contextlib.suppress(json.JSONDecodeError):
                custom_settings = json.loads(data["custom_settings"])

        return cls(
            project_id=data.get("project_id", ""),
            item_schema=item_schema,
            dedup_fields=dedup_fields,
            ttl_seconds=int(data.get("ttl_seconds", 0)),
            max_items=int(data.get("max_items", 0)),
            post_processors=post_processors,
            custom_settings=custom_settings,
        )
