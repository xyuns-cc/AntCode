"""
数据库查询优化工具
提供批量操作、查询优化和缓存功能
"""

import asyncio

from loguru import logger


class DatabaseOptimizer:
    """数据库查询优化器"""

    @staticmethod
    async def bulk_get_or_create(
        model_class,
        items,
        unique_fields,
        batch_size = 100
    ):
        """
        批量获取或创建对象
        
        Args:
            model_class: 模型类
            items: 要创建的对象数据列表
            unique_fields: 唯一字段列表
            batch_size: 批处理大小
            
        Returns:
            (created_objects, existing_objects)
        """
        created_objects = []
        existing_objects = []

        # 分批处理
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]

            # 构建查询条件
            query_conditions = []
            for item in batch:
                condition = {}
                for field in unique_fields:
                    if field in item:
                        condition[field] = item[field]
                if condition:
                    query_conditions.append(condition)

            # 查询已存在的对象
            if query_conditions:
                existing_query = model_class.filter()
                for condition in query_conditions:
                    existing_query = existing_query.union(
                        model_class.filter(**condition)
                    )

                try:
                    existing = await existing_query.all()
                    existing_objects.extend(existing)
                except Exception as e:
                    logger.warning(f"查询已存在对象失败: {e}")
                    existing = []

            # 找出需要创建的对象
            existing_keys = set()
            for obj in existing:
                key = tuple(getattr(obj, field) for field in unique_fields)
                existing_keys.add(key)

            to_create = []
            for item in batch:
                key = tuple(item.get(field) for field in unique_fields)
                if key not in existing_keys:
                    to_create.append(item)

            # 批量创建
            if to_create:
                try:
                    new_objects = await model_class.bulk_create([
                        model_class(**item) for item in to_create
                    ])
                    created_objects.extend(new_objects)
                except Exception as e:
                    logger.error(f"批量创建失败: {e}")
                    # 回退到逐个创建
                    for item in to_create:
                        try:
                            obj = await model_class.create(**item)
                            created_objects.append(obj)
                        except Exception as create_error:
                            logger.error(f"创建对象失败: {create_error}")

        logger.info(f"批量操作完成: 创建 {len(created_objects)} 个对象, 找到 {len(existing_objects)} 个已存在对象")
        return created_objects, existing_objects

    @staticmethod
    async def bulk_update(
        model_class,
        updates,
        key_field = 'id',
        batch_size = 100
    ):
        """
        批量更新对象
        
        Args:
            model_class: 模型类
            updates: 更新数据列表，每项包含key_field和要更新的字段
            key_field: 用于标识对象的字段名
            batch_size: 批处理大小
            
        Returns:
            更新的对象数量
        """
        updated_count = 0

        # 分批处理
        for i in range(0, len(updates), batch_size):
            batch = updates[i:i + batch_size]

            # 获取要更新的对象ID
            keys = [item[key_field] for item in batch if key_field in item]

            if not keys:
                continue

            # 批量获取对象
            objects = await model_class.filter(**{f"{key_field}__in": keys}).all()
            objects_dict = {getattr(obj, key_field): obj for obj in objects}

            # 应用更新
            updated_objects = []
            for update_data in batch:
                key_value = update_data.get(key_field)
                if key_value in objects_dict:
                    obj = objects_dict[key_value]
                    for field, value in update_data.items():
                        if field != key_field:
                            setattr(obj, field, value)
                    updated_objects.append(obj)

            # 批量保存
            if updated_objects:
                try:
                    await model_class.bulk_update(
                        updated_objects, 
                        fields=[field for field in updates[0].keys() if field != key_field]
                    )
                    updated_count += len(updated_objects)
                except Exception as e:
                    logger.error(f"批量更新失败: {e}")
                    # 回退到逐个更新
                    for obj in updated_objects:
                        try:
                            await obj.save()
                            updated_count += 1
                        except Exception as save_error:
                            logger.error(f"保存对象失败: {save_error}")

        logger.info(f"批量更新完成: 更新 {updated_count} 个对象")
        return updated_count

    @staticmethod
    async def optimized_paginate(
        queryset,
        page,
        size,
        use_cursor = False,
        cursor_field = 'id'
    ):
        """
        优化的分页查询
        
        Args:
            queryset: 查询集
            page: 页码
            size: 页大小
            use_cursor: 是否使用游标分页
            cursor_field: 游标字段
            
        Returns:
            (objects, total_count, next_cursor)
        """
        if use_cursor:
            # 游标分页，性能更好
            objects = await queryset.limit(size + 1).all()
            has_more = len(objects) > size
            if has_more:
                objects = objects[:-1]

            next_cursor = None
            if has_more and objects:
                next_cursor = str(getattr(objects[-1], cursor_field))

            # 游标分页不返回总数（避免性能问题）
            return objects, -1, next_cursor
        else:
            # 传统分页
            offset = (page - 1) * size

            # 并行获取数据和总数
            objects_task = queryset.offset(offset).limit(size).all()
            count_task = queryset.count()

            objects, total_count = await asyncio.gather(objects_task, count_task)

            return objects, total_count, None

    @staticmethod
    async def batch_delete(
        model_class,
        filters,
        batch_size = 1000
    ):
        """
        批量删除对象
        
        Args:
            model_class: 模型类
            filters: 过滤条件
            batch_size: 批处理大小
            
        Returns:
            删除的对象数量
        """
        deleted_count = 0

        while True:
            # 获取一批要删除的对象ID
            objects = await model_class.filter(**filters).limit(batch_size).values_list('id', flat=True)

            if not objects:
                break

            # 批量删除
            batch_deleted = await model_class.filter(id__in=objects).delete()
            deleted_count += batch_deleted

            logger.debug(f"批量删除了 {batch_deleted} 个对象")

            # 如果这批少于batch_size，说明删除完了
            if len(objects) < batch_size:
                break

        logger.info(f"批量删除完成: 共删除 {deleted_count} 个对象")
        return deleted_count


# ==================== ORM 序列化工具 ====================

class ORMProxy:
    """ORM 对象代理，支持属性和字典访问"""
    __slots__ = ('_data',)

    def __init__(self, data: dict):
        object.__setattr__(self, '_data', {})
        for k, v in data.items():
            if isinstance(v, dict) and '__type__' not in v:
                self._data[k] = ORMProxy(v)
            else:
                self._data[k] = v

    def __getattr__(self, key):
        return self._data.get(key)

    def __getitem__(self, key):
        return self._data.get(key)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def model_dump(self, **kwargs) -> dict:
        return {k: (v.model_dump(**kwargs) if isinstance(v, ORMProxy) else v) for k, v in self._data.items()}

    def dict(self, **kwargs) -> dict:
        return self.model_dump(**kwargs)

    def __repr__(self):
        return f"ORMProxy({self._data})"


def _serialize_orm_object(obj) -> dict:
    """序列化 ORM 对象"""
    from tortoise import Model
    if not isinstance(obj, Model):
        return obj
    return {
        '__type__': 'tortoise_model',
        '__data__': {f: getattr(obj, f, None) for f in obj._meta.db_fields}
    }


def _serialize_result(result):
    """序列化查询结果"""
    from tortoise import Model

    if isinstance(result, Model):
        return _serialize_orm_object(result)
    if isinstance(result, (list, tuple)):
        return {'__type__': type(result).__name__, '__data__': [_serialize_result(i) for i in result]}
    if isinstance(result, dict):
        return {k: _serialize_result(v) for k, v in result.items()}
    return result


def _deserialize_result(data):
    """反序列化查询结果"""
    if not isinstance(data, dict):
        return [_deserialize_result(i) for i in data] if isinstance(data, list) else data

    dtype = data.get('__type__')
    if dtype == 'tortoise_model':
        return ORMProxy(data['__data__'])
    if dtype == 'list':
        return [_deserialize_result(i) for i in data['__data__']]
    if dtype == 'tuple':
        return tuple(_deserialize_result(i) for i in data['__data__'])
    return {k: _deserialize_result(v) for k, v in data.items()}


# ==================== 查询缓存装饰器 ====================

def cached_query(ttl: int = 300, namespace: str = None):
    """
    查询缓存装饰器 - 自动处理 ORM 序列化
    
    Args:
        ttl: 缓存 TTL（秒）
        namespace: 缓存命名空间
    """
    from functools import wraps
    from src.utils.hash_utils import calculate_content_hash

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            from src.infrastructure.cache import query_cache

            # 生成缓存键
            try:
                filtered = {k: v for k, v in kwargs.items() if not hasattr(v, '__dict__') or not hasattr(v, 'url')}
                content = f"{func.__module__}.{func.__name__}:{args}:{sorted(filtered.items())}"
                raw_key = calculate_content_hash(content)[:16]
                cache_key = f"{namespace}:{raw_key}" if namespace else raw_key
            except Exception:
                return await func(*args, **kwargs)

            # 尝试缓存
            try:
                if cached := await query_cache.get(cache_key):
                    return _deserialize_result(cached)
            except Exception:
                pass

            result = await func(*args, **kwargs)

            try:
                await query_cache.set(cache_key, _serialize_result(result), ttl)
            except Exception:
                pass

            return result
        return wrapper
    return decorator