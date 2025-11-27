"""数据库查询优化工具"""
import asyncio
import hashlib
from functools import wraps

from loguru import logger

from src.core.cache import query_cache


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

def _serialize_orm_object(obj):
    """将 Tortoise ORM 对象序列化为字典（保留类型信息）"""
    from tortoise import Model
    
    if isinstance(obj, Model):
        # 序列化单个 ORM 对象
        data = {}
        for field_name in obj._meta.db_fields:
            value = getattr(obj, field_name, None)
            data[field_name] = value
        
        # 添加类型元信息
        return {
            '__type__': 'tortoise_model',
            '__model__': f"{obj.__class__.__module__}.{obj.__class__.__name__}",
            '__data__': data
        }
    
    return obj


def _deserialize_orm_object(data):
    """将字典反序列化为 Tortoise ORM 对象（模拟对象）"""
    if isinstance(data, dict) and data.get('__type__') == 'tortoise_model':
        # 创建一个简单的对象，模拟 ORM 对象的属性访问
        class ORMProxy:
            """ORM 对象代理，支持属性访问和字典访问"""
            def __init__(self, data_dict):
                # 递归处理嵌套的字典，确保所有属性都可访问
                for key, value in data_dict.items():
                    if isinstance(value, dict) and '__type__' not in value:
                        # 普通字典也转换为支持属性访问的对象
                        self.__dict__[key] = ORMProxy(value)
                    else:
                        self.__dict__[key] = value
            
            def __getitem__(self, key):
                return self.__dict__.get(key)
            
            def get(self, key, default=None):
                return self.__dict__.get(key, default)
            
            def __repr__(self):
                return f"ORMProxy({self.__dict__})"
            
            # 添加 model_dump 方法支持 Pydantic 序列化
            def model_dump(self, **kwargs):
                """兼容 Pydantic 的 model_dump 方法"""
                result = {}
                for key, value in self.__dict__.items():
                    if hasattr(value, 'model_dump'):
                        result[key] = value.model_dump(**kwargs)
                    elif isinstance(value, ORMProxy):
                        result[key] = value.model_dump(**kwargs)
                    else:
                        result[key] = value
                return result
            
            # 添加 dict 方法支持 Pydantic v1
            def dict(self, **kwargs):
                """兼容 Pydantic v1 的 dict 方法"""
                return self.model_dump(**kwargs)
        
        return ORMProxy(data['__data__'])
    
    return data


def _serialize_result(result):
    """智能序列化查询结果（支持单个对象、列表、元组等）"""
    from tortoise import Model
    
    if isinstance(result, Model):
        # 单个 ORM 对象
        return _serialize_orm_object(result)
    
    elif isinstance(result, (list, tuple)):
        # 列表或元组
        serialized = [_serialize_result(item) for item in result]
        return {
            '__type__': 'list' if isinstance(result, list) else 'tuple',
            '__data__': serialized
        }
    
    elif isinstance(result, dict):
        # 字典（可能包含 ORM 对象）
        return {k: _serialize_result(v) for k, v in result.items()}
    
    else:
        # 基本类型（int, str, None等）
        return result


def _deserialize_result(data):
    """智能反序列化查询结果"""
    if isinstance(data, dict):
        # 检查是否是特殊类型标记
        if data.get('__type__') == 'tortoise_model':
            return _deserialize_orm_object(data)
        
        elif data.get('__type__') == 'list':
            return [_deserialize_result(item) for item in data['__data__']]
        
        elif data.get('__type__') == 'tuple':
            return tuple(_deserialize_result(item) for item in data['__data__'])
        
        else:
            # 普通字典，递归处理值
            return {k: _deserialize_result(v) for k, v in data.items()}
    
    elif isinstance(data, list):
        # 列表，递归处理
        return [_deserialize_result(item) for item in data]
    
    else:
        # 基本类型
        return data


# ==================== 查询缓存装饰器 ====================

def cached_query(ttl = 300, namespace = None):
    """
    智能查询缓存装饰器 - 自动处理 ORM 对象序列化
    
    功能：
    1. 自动序列化 Tortoise ORM 对象为字典
    2. 从缓存读取时自动反序列化为类ORM对象
    3. 保持属性访问接口一致（支持 .id 和 ['id'] 两种方式）
    4. 支持单对象、列表、元组等复杂结果类型
    
    Args:
        ttl: 缓存时间（秒）
        namespace: 缓存命名空间前缀，用于按前缀清除缓存（如 'project:list'）
    
    示例：
        @cached_query(ttl=300, namespace="project:list")
        async def get_projects_list(...):
            return projects, total
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            func_name = f"{func.__module__}.{func.__name__}"
            
            # 创建稳定的缓存键
            try:
                # 过滤掉不可序列化的参数
                filtered_kwargs = {}
                for k, v in kwargs.items():
                    try:
                        str(v)
                        filtered_kwargs[k] = v
                    except:
                        continue
                
                key_content = f"{func_name}:{args}:{sorted(filtered_kwargs.items())}"
                raw_key = hashlib.md5(key_content.encode()).hexdigest()[:16]
                
                # 添加namespace前缀
                if namespace:
                    cache_key = f"{namespace}:{raw_key}"
                else:
                    cache_key = raw_key
            except Exception as e:
                logger.warning(f"生成缓存键失败: {e}，跳过缓存")
                return await func(*args, **kwargs)
            
            # 尝试从缓存获取
            try:
                cached_data = await query_cache.get(cache_key)
                if cached_data is not None:
                    # 反序列化缓存数据
                    result = _deserialize_result(cached_data)
                    logger.debug(f"查询缓存命中: {func.__name__} (key: {cache_key})")
                    return result
            except Exception as e:
                logger.warning(f"读取缓存失败: {e}")
            
            # 执行查询
            result = await func(*args, **kwargs)
            
            # 序列化并缓存结果
            try:
                serialized = _serialize_result(result)
                await query_cache.set(cache_key, serialized, ttl)
                logger.debug(f"查询结果已缓存: {func.__name__} (key: {cache_key})")
            except Exception as e:
                logger.warning(f"保存查询缓存失败: {e}")
            
            return result
        
        return wrapper
    return decorator