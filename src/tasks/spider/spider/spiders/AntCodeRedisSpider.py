from copy import deepcopy
import ujson
from scrapy_redis.spiders import RedisSpider, bytes_to_str
from scrapy.http import Request, FormRequest
from spider.spider.utils.base import ScheduledRequest
from spider.spider.utils.log import logger
from typing import Optional, Dict, Any

class AntCodeRedisSpider(RedisSpider):

    SUPPORTED_FETCH_TYPES = {'requests', 'browser', 'curl_cffi'}
    DEFAULT_FETCH_TYPE = 'requests'

    def get_callback(self, callback):
        """
        获取回调函数
        :param callback: 回调函数名
        :return: 返回一个元组(func, bool)
        """
        return None, False

    def make_request_from_data(self, data) -> Optional[Request]:
        """
        从Redis数据创建请求对象
        支持通过fetch_type或meta字段指定爬取方式
        
        :param data: Redis中的任务数据
        :return: Scrapy Request对象或None
        """
        try:
            # 解析JSON数据
            task_data = ujson.loads(
                bytes_to_str(data, self.redis_encoding)
            )
            
            # 验证必需字段
            if not task_data.get('url'):
                logger.error("任务数据缺少url字段")
                return None
            
            scheduled = ScheduledRequest(**task_data)
            
        except ujson.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}, 数据: {data[:100]}...")
            return None
        except Exception as e:
            logger.error(f"解析Redis数据失败: {e}")
            return None

        # 获取回调函数
        callback_result = self.get_callback(scheduled.callback)
        if not callback_result:
            logger.error(f"未找到回调函数: {scheduled.callback}")
            return None
        
        callback, dont_filter = callback_result
        if not callable(callback):
            logger.error(f"{scheduled.callback}不是可调用的函数")
            return None

        # 确定爬取方式
        fetch_type = self._determine_fetch_type(scheduled)
        
        # 构建基础请求参数
        meta = deepcopy(scheduled.meta or {})
        params = {
            "url": scheduled.url,
            "method": scheduled.method,
            "meta": meta,
            "callback": callback,
            "dont_filter": getattr(scheduled, 'dont_filter', dont_filter),
            "priority": getattr(scheduled, 'priority', 0),
            "headers": deepcopy(getattr(scheduled, 'headers', {}) or {}),
            "cookies": deepcopy(getattr(scheduled, 'cookies', {}) or {}),
        }

        if getattr(scheduled, 'proxy', None) and 'proxy' not in params["meta"]:
            params["meta"]["proxy"] = scheduled.proxy
        
        # 在meta中设置fetch_type，供中间件使用
        params["meta"]["fetch_type"] = fetch_type
        
        # 记录请求信息
        logger.info(f"创建请求: {scheduled.url} (fetch_type: {fetch_type})")
        
        # 根据爬取方式和请求方法创建相应的请求对象
        return self._create_request_by_type(fetch_type, scheduled, params)
    
    def _determine_fetch_type(self, scheduled) -> str:
        """
        从meta中获取fetch_type字段确定爬取方式
        
        :param scheduled: ScheduledRequest对象
        :return: 爬取方式字符串
        """
        if scheduled.meta and 'fetch_type' in scheduled.meta:
            fetch_type = scheduled.meta['fetch_type']
            if fetch_type in self.SUPPORTED_FETCH_TYPES:
                return fetch_type
            else:
                logger.warning(f"不支持的fetch_type: {fetch_type}，使用默认方式: {self.DEFAULT_FETCH_TYPE}")
        
        # 检查是否是点击分页，如果是则强制使用browser
        if scheduled.meta and scheduled.meta.get('pagination', {}).get('method') == 'click_element':
            logger.info("检测到点击分页，自动使用browser模式")
            return 'browser'
        
        return self.DEFAULT_FETCH_TYPE
    
    def _create_request_by_type(self, fetch_type: str, scheduled: ScheduledRequest, params: Dict[str, Any]) -> Request:
        """
        根据爬取方式创建相应的请求对象
        
        :param fetch_type: 爬取方式
        :param scheduled: ScheduledRequest对象
        :param params: 请求参数
        :return: Scrapy Request对象
        """
        post_body = getattr(scheduled, 'body', None)
        method = (scheduled.method or "").upper()

        if method == "POST":
            if fetch_type == "browser":
                logger.error(
                    "任务被丢弃：fetch_type=browser 不支持 POST 请求 -> %s",
                    scheduled.url,
                )
                return None

            if post_body is not None:
                if fetch_type == "requests":
                    if isinstance(post_body, dict):
                        formdata = {
                            str(key): "" if value is None else str(value)
                            for key, value in post_body.items()
                        }
                        return FormRequest(formdata=formdata, **params)
                    else:
                        if isinstance(post_body, (bytes, bytearray)):
                            body_bytes = bytes(post_body)
                        else:
                            body_bytes = str(post_body).encode("utf-8")
                        headers = params.setdefault("headers", {})
                        headers.setdefault("Content-Type", "application/json")
                        params["body"] = body_bytes
                else:
                    # 对于其他方式，将body数据放入meta中供中间件处理
                    params["meta"]["request_body"] = post_body

        # 根据fetch_type设置特定的meta标识，供相应中间件识别
        if fetch_type != 'requests':
            params["meta"][f"{fetch_type}_request"] = True
            
            # 为不同的爬取方式设置特定配置
            self._set_fetch_type_config(fetch_type, params, scheduled)
        
        return Request(**params)
    
    def _set_fetch_type_config(self, fetch_type: str, params: Dict[str, Any], scheduled: ScheduledRequest) -> None:
        """
        为不同的爬取方式设置特定配置
        
        :param fetch_type: 爬取方式
        :param params: 请求参数
        :param scheduled: ScheduledRequest对象
        """
        meta = params["meta"]
        
        if fetch_type == 'browser':
            # 浏览器渲染配置
            meta.setdefault('browser_options', {
                'headless': True,
                'window_size': '1920,1080'
            })
            # 根据是否有分页设置等待时间
            if meta.get('pagination', {}).get('method') == 'click_element':
                # 点击分页可能需要更长的等待时间
                meta.setdefault('wait_time', 3)
            else:
                meta.setdefault('wait_time', 2)
            meta.setdefault('wait_condition', 'presence_of_element_located')
            
        elif fetch_type == 'curl_cffi':
            options = meta.setdefault('curl_cffi_options', {})
            options.setdefault('impersonate', meta.get('impersonate') or scheduled.meta.get('impersonate') or 'chrome120')
            options.setdefault('timeout', meta.get('timeout') or scheduled.meta.get('timeout') or 30)
            options.setdefault('verify', meta.get('verify') if meta.get('verify') is not None else scheduled.meta.get('verify', True))
            options.setdefault('allow_redirects', meta.get('allow_redirects') if meta.get('allow_redirects') is not None else scheduled.meta.get('allow_redirects', True))
            options.setdefault('http2', meta.get('http2') if meta.get('http2') is not None else scheduled.meta.get('http2', False))
            options.setdefault('as_json', options.get('as_json', True))
            
        # 从scheduled对象中复制特定配置
        if hasattr(scheduled, 'meta') and scheduled.meta:
            for key, value in scheduled.meta.items():
                if key.endswith('_options') or key in [
                    'wait_time', 'wait_condition', 'impersonate', 'verify', 'proxy'
                ]:
                    meta[key] = value
