"""敏感任务负载信封的**线协议常量**，与加解密实现分开存放。

放在 `common.security` 之外是刻意的：`common/security/__init__.py` 是聚合式再导出，
会把 `auth` → `common.config` → 模块作用域的 `settings = Settings()` 拖进导入链，
于是"导入这两个常量"变成"必须有 DATABASE_URL"。而只连 Redis 的离线工具
（`scripts.check_ready_streams` / `scripts.migrate_crawl_redis`，跑在生产 compose 的
`crawl-redis-upgrade` 服务里）按最小权限**只拿到 Redis 凭据**，导入即崩。

`common/__init__.py` 已经写明本包不做聚合再导出，因此本模块可以在没有任何控制面
配置的进程里安全导入。边界由 `tests/unit/core/test_config_free_import_boundary.py` 锁住。

字段名与版本是 ready / redispatch 帧的一部分，改动等同于改线协议
（见 `docs/release-runbook.md` 第 2 节 W2）。
"""

from __future__ import annotations

ENVELOPE_FIELD = "sensitive_payload_envelope"
ENVELOPE_VERSION = 2

__all__ = ["ENVELOPE_FIELD", "ENVELOPE_VERSION"]
