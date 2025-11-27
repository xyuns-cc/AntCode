import redis
import json
from urllib.parse import urlparse
from spider.spider.settings import REDIS_URL
from spider.spider.utils.log import logger


class RedisCtrl:

    def __init__(self):
        parsed_url = urlparse(REDIS_URL)
        host = parsed_url.hostname or '127.0.0.1'
        port = parsed_url.port or 6379
        password = parsed_url.password
        
        try:
            self.pool = redis.ConnectionPool(
                host=host,
                port=port,
                password=password,
                max_connections=20,
                socket_timeout=10,
                socket_connect_timeout=10,
                socket_keepalive=True
            )
            self.r = redis.Redis(connection_pool=self.pool)
            self.r.ping()
            logger.info(f"Redis连接池初始化成功 {host}:{port}")
        except redis.AuthenticationError:
            logger.error("Redis认证失败")
            raise
        except redis.ConnectionError as e:
            logger.error(f"Redis连接失败: {e}")
            raise
        except Exception as e:
            logger.error(f"Redis连接错误: {e}")
            raise

    def reqs_push(self, redis_key, reqs):
        """
        :param redis_key:
        :param reqs:
        :return: 将请求推到指定的redis_key中
        """
        try:
            pipe = self.r.pipeline(transaction=True)
            for req in reqs:
                if hasattr(req, "to_dict"):
                    payload = req.to_dict()
                elif hasattr(req, "__dict__"):
                    payload = req.__dict__
                else:
                    payload = req
                pipe.rpush(redis_key, json.dumps(payload, ensure_ascii=False))
            pipe.execute()
        except Exception as e:
            logger.error(f"redis写入失败：{e}")

    def keys_del(self, keys):
        """
        :param keys: list
        :return: 删除redis中的指定key
        """
        try:
            pipe = self.r.pipeline(transaction=True)
            for key in keys:
                self.r.delete(key)
            pipe.execute()
        except Exception as e:
            logger.error(f"redis删除失败：{e}")

    def key_len(self, key):
        """
        :param key:
        :return: 获取redis_key的数量长度
        """
        try:
            size = self.r.llen(key)
            return size
        except Exception as e:
            logger.error(f"redis读取失败：{e}")

    def pop_list(self, key, size):
        """
        :param key:
        :param size: int
        :return: 从_key中pop出指定数量的值
        """
        try:
            pipe = self.r.pipeline(transaction=True)
            vals = []
            for i in range(size):
                top = self.r.lpop(key)
                if top:
                    vals.append(top.decode())
            pipe.execute()
            return vals
        except Exception as e:
            logger.error(f"redis读取失败：{e}")

    def copy(self, source_key, target_key):
        """
        :param source_key:
        :param target_key:
        :return: 备份redis数据，从source_key备份到target_key
        """
        try:
            pipe = self.r.pipeline(transaction=True)
            vals = self.r.lrange(source_key, 0, -1)
            self.r.delete(target_key)
            for val in vals:
                self.r.rpush(target_key, val)
            pipe.execute()
        except Exception as e:
            logger.error(f"redis复制失败：{e}")

    def add_string(self, key, value):
        """
        :param key:
        :param value:
        :return: 写入 redis string
        """
        try:
            self.r.set(key, value)
        except Exception as e:
            logger.error(f"redis写入失败：{e}")

    def get_string(self, key):
        """
        :param key:
        :return: 读取 string
        """
        try:
            value = self.r.get(key)
            return value.decode() if value else None
        except Exception as e:
            logger.error(f"redis读取失败：{e}")

    def add_set(self, key, *args):
        """
        :param key:
        :param value:
        :return: 写入 redis set
        """
        try:
            self.r.sadd(key, *args)
        except Exception as e:
            logger.error(f"redis写入失败：{e}")

    def remove_set(self, key, value):
        """
        :param key: redis_key.key
        :return: 删除redis_key中的指定记录key
        """
        try:
            self.r.srem(key, value)
        except Exception as e:
            logger.error(f"redis删除失败：{e}")

    def get_set(self, key):
        """
        :return: 从redis_key中取出所有值
        """
        try:
            return [i.decode() for i in self.r.smembers(key)]
        except Exception as e:
            logger.error(f"redis读取失败：{e}")

    def add_hash(self, redis_key, key, value):
        try:
            self.r.hset(redis_key, key, value)
        except Exception as e:
            logger.error(f"redis写入失败：{e}")

    def remove_hash(self, redis_key, key):
        try:
            self.r.hdel(redis_key, key)
        except Exception as e:
            logger.error(f"redis删除失败：{e}")

    def get_hash(self, redis_key, key):
        try:
            return self.r.hget(redis_key, key.encode()).decode()
        except Exception as e:
            logger.error(f"redis读取失败：{e}")

    def get_hashall(self, redis_key):
        try:
            dicts = self.r.hgetall(redis_key)
            return {key.decode(): val.decode() for key, val in dicts.items()}
        except Exception as e:
            logger.error(f"redis读取失败：{e}")