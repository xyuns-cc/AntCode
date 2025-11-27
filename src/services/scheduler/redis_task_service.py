# src/services/redis_task_service.py (完整版本)
"""Redis任务提交服务"""
import random
import string
from datetime import datetime

import redis.asyncio as redis
import ujson
from loguru import logger

from src.core.config import settings
from src.core.exceptions import TaskExecutionException
from src.models.enums import CrawlEngine, RequestMethod
from src.utils.redis_pool import get_redis_client


class RedisTaskService:
    """Redis任务提交服务"""

    def __init__(self):
        self.queue_name = settings.REDIS_TASK_QUEUE

    async def get_client(self):
        """获取Redis客户端"""
        return await get_redis_client()

    async def connect(self):
        """连接Redis（兼容性方法，实际使用连接池）"""
        # 测试连接
        client = await self.get_client()
        await client.ping()
        logger.info("Redis连接测试成功")

    async def disconnect(self):
        """断开Redis连接（兼容性方法，连接池自动管理）"""
        # 连接池会自动管理连接，这里只是兼容性方法
        logger.debug("Redis连接池自动管理连接，无需手动断开")

    async def is_connected(self):
        """检查是否已连接"""
        try:
            client = await self.get_client()
            await client.ping()
            return True
        except Exception:
            return False

    def generate_task_id(self, spider_name):
        """生成任务ID"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
        random_hex = ''.join(random.choices(string.hexdigits.lower(), k=7))
        return f"{spider_name}-{timestamp}-{random_hex}"

    async def submit_rule_task(
            self,
            project,
            rule_detail,
            execution_id,
            params = None
    ):
        """提交规则任务到Redis"""
        try:
            # 获取Redis客户端
            client = await self.get_client()

            # 构建任务JSON
            task_json = await self._build_task_json(project, rule_detail, execution_id, params)

            # 序列化任务数据
            task_data = ujson.dumps(task_json, ensure_ascii=False).encode('utf-8')

            # 提交到Redis队列（使用LPUSH，任务从右端进入，从左端取出）
            await client.lpush(self.queue_name, task_data)

            # 获取队列长度
            queue_length = await client.llen(self.queue_name)

            logger.info(f"任务已提交到Redis")
            logger.info(f"   任务ID: {task_json['meta']['task_id']}")
            logger.info(f"   队列: {self.queue_name}")
            logger.info(f"   当前队列长度: {queue_length}")

            return {
                "success": True,
                "task_id": task_json["meta"]["task_id"],
                "queue": self.queue_name,
                "queue_length": queue_length,
                "task": task_json
            }

        except redis.RedisError as e:
            error_msg = f"Redis操作失败: {e}"
            logger.error(error_msg)
            raise TaskExecutionException(error_msg)
        except ujson.JSONEncodeError as e:
            error_msg = f"任务序列化失败: {e}"
            logger.error(error_msg)
            raise TaskExecutionException(error_msg)
        except Exception as e:
            error_msg = f"提交任务失败: {e}"
            logger.error(error_msg)
            raise TaskExecutionException(error_msg)

    async def submit_batch_tasks(
            self,
            tasks
    ):
        """批量提交任务到Redis"""
        try:
            if not await self.is_connected():
                await self.connect()

            if not self.redis_client:
                return {
                    "success": False,
                    "message": "Redis未连接"
                }

            # 使用管道批量提交
            pipe = self.redis_client.pipeline()
            task_ids = []

            for task_json in tasks:
                task_data = ujson.dumps(task_json, ensure_ascii=False).encode('utf-8')
                pipe.lpush(self.queue_name, task_data)
                task_ids.append(task_json.get("meta", {}).get("task_id"))

            # 执行管道
            await pipe.execute()

            # 获取队列长度
            queue_length = await self.redis_client.llen(self.queue_name)

            logger.info(f"批量提交 {len(tasks)} 个任务到Redis")
            logger.info(f"   队列: {self.queue_name}")
            logger.info(f"   当前队列长度: {queue_length}")

            return {
                "success": True,
                "task_count": len(tasks),
                "task_ids": task_ids,
                "queue": self.queue_name,
                "queue_length": queue_length
            }

        except Exception as e:
            logger.error(f"批量提交任务失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def get_queue_info(self):
        """获取队列信息"""
        try:
            if not await self.is_connected():
                await self.connect()

            if not self.redis_client:
                return {
                    "success": False,
                    "message": "Redis未连接"
                }

            queue_length = await self.redis_client.llen(self.queue_name)

            # 获取前几个任务的预览（不移除）
            preview_count = min(5, queue_length)
            preview_tasks = []
            if preview_count > 0:
                raw_tasks = await self.redis_client.lrange(self.queue_name, 0, preview_count - 1)
                for raw_task in raw_tasks:
                    try:
                        task = ujson.loads(raw_task.decode('utf-8'))
                        preview_tasks.append({
                            "task_id": task.get("meta", {}).get("task_id"),
                            "url": task.get("url"),
                            "project_name": task.get("meta", {}).get("project_name"),
                            "created_at": task.get("meta", {}).get("created_at")
                        })
                    except:
                        pass

            # 获取Redis服务器信息
            info = await self.redis_client.info()
            memory_used = info.get('used_memory_human', 'N/A')
            connected_clients = info.get('connected_clients', 'N/A')

            return {
                "success": True,
                "queue_name": self.queue_name,
                "queue_length": queue_length,
                "preview_tasks": preview_tasks,
                "server_info": {
                    "memory_used": memory_used,
                    "connected_clients": connected_clients
                }
            }

        except Exception as e:
            logger.error(f"获取队列信息失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def remove_task(self, task_id):
        """从队列中移除特定任务"""
        try:
            if not await self.is_connected():
                await self.connect()

            if not self.redis_client:
                return {
                    "success": False,
                    "message": "Redis未连接"
                }

            # 获取所有任务
            raw_tasks = await self.redis_client.lrange(self.queue_name, 0, -1)
            removed_count = 0

            for raw_task in raw_tasks:
                try:
                    task = ujson.loads(raw_task.decode('utf-8'))
                    if task.get("meta", {}).get("task_id") == task_id:
                        # 移除该任务
                        await self.redis_client.lrem(self.queue_name, 1, raw_task)
                        removed_count += 1
                except:
                    pass

            if removed_count > 0:
                logger.info(f"从队列中移除任务: {task_id}")
                return {
                    "success": True,
                    "message": f"成功移除任务",
                    "task_id": task_id,
                    "removed_count": removed_count
                }
            else:
                return {
                    "success": False,
                    "message": "任务不存在",
                    "task_id": task_id
                }

        except Exception as e:
            logger.error(f"移除任务失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def clear_queue(self):
        """清空队列（谨慎使用）"""
        try:
            if not await self.is_connected():
                await self.connect()

            if not self.redis_client:
                return {
                    "success": False,
                    "message": "Redis未连接"
                }

            # 获取当前队列长度
            current_length = await self.redis_client.llen(self.queue_name)

            # 清空队列
            await self.redis_client.delete(self.queue_name)

            logger.warning(f"已清空Redis队列 '{self.queue_name}'，删除了 {current_length} 个任务")

            return {
                "success": True,
                "message": f"队列已清空",
                "deleted_count": current_length
            }

        except Exception as e:
            logger.error(f"清空队列失败: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _build_task_json(
            self,
            project,
            rule_detail,
            execution_id,
            params = None
    ):
        """构建任务JSON"""
        # 生成任务ID
        spider_name = params.get("spider_name", project.name) if params else project.name
        # 清理spider_name中的特殊字符
        spider_name = ''.join(c for c in spider_name if c.isalnum() or c in ('_', '-'))
        task_id = self.generate_task_id(spider_name)

        # 获取worker_id
        worker_id = params.get("worker_id", settings.WORKER_ID) if params else settings.WORKER_ID

        # 基础任务结构
        task = {
            "url": rule_detail.target_url,
            "callback": rule_detail.callback_type.value if hasattr(rule_detail.callback_type,
                                                                   'value') else rule_detail.callback_type,
            "method": rule_detail.request_method.value if hasattr(rule_detail.request_method,
                                                                  'value') else rule_detail.request_method,
            "meta": {
                "fetch_type": self._map_engine_to_fetch_type(rule_detail.engine),
                "task_id": task_id,
                "worker_id": worker_id,
                "execution_id": execution_id,
                "project_id": project.id,
                "project_name": project.name,
                "created_at": datetime.now().isoformat(),
                "spider_name": spider_name
            },
            "headers": rule_detail.headers or {},
            "cookies": rule_detail.cookies or {},
            "priority": rule_detail.priority or 0,
            "dont_filter": rule_detail.dont_filter if rule_detail.dont_filter is not None else False
        }

        # 添加请求体（如果是POST请求）
        if rule_detail.request_method in [RequestMethod.POST, "POST"]:
            if rule_detail.request_body:
                task["data"] = rule_detail.request_body

        # 添加代理配置
        if rule_detail.proxy_config:
            proxy = rule_detail.proxy_config.get("proxy")
            if proxy:
                task["meta"]["proxy"] = proxy
                # 如果有代理认证
                proxy_auth = rule_detail.proxy_config.get("auth")
                if proxy_auth:
                    task["meta"]["proxy_auth"] = proxy_auth

        # 处理提取规则
        rules = self._build_extraction_rules(rule_detail)
        if rules:
            task["meta"]["rules"] = rules

        # 处理分页配置
        pagination = self._build_pagination_config(rule_detail, task["url"])
        if pagination:
            task["meta"]["pagination"] = pagination
            # 如果是URL分页，添加页码
            if pagination.get("method") == "url_pattern" and params and "page_number" in params:
                task["meta"]["page_number"] = params["page_number"]
                # 替换URL中的页码占位符
                if "{page}" in task["url"]:
                    task["url"] = task["url"].replace("{page}", str(params["page_number"]))
                elif "{}" in task["url"]:
                    task["url"] = task["url"].format(params["page_number"])

        # 添加浏览器配置（如果使用浏览器引擎）
        if rule_detail.engine == CrawlEngine.BROWSER:
            browser_config = {
                "headless": True,
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": rule_detail.headers.get("User-Agent") if rule_detail.headers else None,
                "wait_until": "networkidle",
                "timeout": 30000
            }

            # 如果有等待时间配置
            if rule_detail.wait_time:
                browser_config["wait_time"] = rule_detail.wait_time * 1000  # 转换为毫秒

            # 如果有JavaScript代码要执行
            if rule_detail.javascript_code:
                browser_config["execute_js"] = rule_detail.javascript_code

            task["meta"]["browser_config"] = browser_config

        # 添加额外参数
        if params:
            for key, value in params.items():
                if key not in ["spider_name", "worker_id", "page_number"]:
                    task["meta"][key] = value

        return task

    def _map_engine_to_fetch_type(self, engine):
        """映射采集引擎到fetch_type"""
        if hasattr(engine, 'value'):
            engine = engine.value

        mapping = {
            CrawlEngine.BROWSER: "browser",
            CrawlEngine.REQUESTS: "requests",
            CrawlEngine.CURL_CFFI: "curl_cffi",
            "browser": "browser",
            "requests": "requests",
            "curl_cffi": "curl_cffi"
        }
        return mapping.get(engine, "requests")

    def _build_extraction_rules(self, rule_detail):
        """构建提取规则数组"""
        # 只使用extraction_rules字段
        if hasattr(rule_detail, 'extraction_rules') and rule_detail.extraction_rules:
            # 如果项目是混合模式，需要根据当前任务类型过滤规则
            rules = rule_detail.extraction_rules
            
            # 对于混合模式项目，可以根据任务上下文决定使用哪些规则
            # 这里先返回所有规则，由爬虫引擎根据page_type字段处理
            return rules
        
        return []

    def _build_pagination_config(self, rule_detail, url):
        """构建分页配置"""
        # 只使用pagination_config字段
        if hasattr(rule_detail, 'pagination_config') and rule_detail.pagination_config:
            return rule_detail.pagination_config
        
        return None


# 全局Redis任务服务实例
redis_task_service = RedisTaskService()
