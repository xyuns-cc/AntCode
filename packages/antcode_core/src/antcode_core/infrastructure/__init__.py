"""
Infrastructure 模块

基础设施适配：
- db: 数据库配置（Tortoise ORM）
- redis: Redis 客户端（连接池、Streams、分布式锁）
- postgres: PostgreSQL 二进制产物存储

本包不做子模块聚合导入：``from antcode_core.infrastructure import db, redis``
会让任何 ``import antcode_core.infrastructure.redis.keys`` 连带加载 Tortoise
配置并实例化控制面 ``Settings()``。调用方直接导入需要的子模块。
"""
