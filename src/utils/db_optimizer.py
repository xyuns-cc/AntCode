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
                except:
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


# 装饰器：自动缓存查询结果
def cached_query(ttl = 300):
    """
    查询缓存装饰器 - 使用统一缓存系统
    
    Args:
        ttl: 缓存时间（秒）
    """
    def decorator(func):
        from functools import wraps
        import hashlib
        from src.core.cache import query_cache
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            func_name = f"{func.__module__}.{func.__name__}"
            
            # 创建更稳定的缓存键
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
                cache_key = hashlib.md5(key_content.encode()).hexdigest()[:16]
            except Exception as e:
                logger.warning(f"生成缓存键失败: {e}，跳过缓存")
                return await func(*args, **kwargs)
            
            # 尝试从缓存获取
            try:
                cached_result = await query_cache.get(cache_key)
                if cached_result is not None:
                    logger.debug(f"数据库查询缓存命中: {func.__name__}")
                    return cached_result
            except Exception as e:
                logger.warning(f"读取缓存失败: {e}")
            
            # 执行查询
            result = await func(*args, **kwargs)
            
            # 缓存结果
            try:
                await query_cache.set(cache_key, result, ttl)
                logger.debug(f"数据库查询结果已缓存: {func.__name__}")
            except Exception as e:
                logger.warning(f"保存查询缓存失败: {e}")
            
            return result
        
        return wrapper
    return decorator