# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy
import hashlib
from typing import Dict, Any, List, Optional
from datetime import datetime


class BaseItem(scrapy.Item):
    """基础Item类，包含通用字段"""
    # URL作为主键
    url = scrapy.Field()
    
    # 任务相关信息
    task_id = scrapy.Field()
    worker_id = scrapy.Field()
    
    # 爬取时间
    crawl_time = scrapy.Field()
    
    # 数据类型标识
    data_type = scrapy.Field()  # 'list' or 'detail'
    
    # 爬取方式
    fetch_type = scrapy.Field()  # 'requests', 'browser', 'curl_cffi'
    
    # 响应状态
    status_code = scrapy.Field()
    
    # 原始提取的数据（字典格式）
    raw_data = scrapy.Field()


class ListPageItem(BaseItem):
    """列表页Item"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self['data_type'] = 'list'
        self['crawl_time'] = datetime.now().isoformat()
    
    # 列表页特有字段
    extracted_data = scrapy.Field()  # 从列表页提取的数据
    detail_urls = scrapy.Field()  # 提取到的详情页URL列表
    page_number = scrapy.Field()  # 当前页码（如果有分页）
    total_items = scrapy.Field()  # 本页提取的条目数量
    
    def to_kafka_message(self) -> Dict[str, Any]:
        """
        转换为Kafka消息格式
        Key格式: task_id:data_type:url_hash
        Value包含完整的URL和其他数据
        """
        # 生成URL的短哈希（8位）
        url_hash = hashlib.md5(self['url'].encode()).hexdigest()[:8]
        # 构建key: task_id:list:url_hash
        kafka_key = f"{self.get('task_id', 'unknown')}:list:{url_hash}"
        
        return {
            'key': kafka_key,  # Kafka消息的key
            'value': {
                'url': self['url'],  # 保留完整的URL在value中
                'data_type': self['data_type'],
                'task_id': self.get('task_id'),
                'worker_id': self.get('worker_id'),
                'crawl_time': self['crawl_time'],
                'fetch_type': self.get('fetch_type', 'requests'),
                'status_code': self.get('status_code', 200),
                'page_number': self.get('page_number'),
                'extracted_data': self.get('extracted_data', {}),
                'detail_urls': self.get('detail_urls', []),
                'total_items': len(self.get('detail_urls', [])),
                'raw_data': self.get('raw_data', {})
            }
        }


class DetailPageItem(BaseItem):
    """详情页Item"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self['data_type'] = 'detail'
        self['crawl_time'] = datetime.now().isoformat()
    
    # 详情页特有字段
    list_data = scrapy.Field()  # 从列表页传递过来的数据
    detail_data = scrapy.Field()  # 从详情页提取的数据
    source_list_url = scrapy.Field()  # 来源列表页URL
    
    def to_kafka_message(self) -> Dict[str, Any]:
        """
        转换为Kafka消息格式
        Key格式: task_id:data_type:url_hash
        Value包含完整的URL和其他数据
        """
        # 生成URL的短哈希（8位）
        url_hash = hashlib.md5(self['url'].encode()).hexdigest()[:8]
        # 构建key: task_id:detail:url_hash
        kafka_key = f"{self.get('task_id', 'unknown')}:detail:{url_hash}"
        
        return {
            'key': kafka_key,  # Kafka消息的key
            'value': {
                'url': self['url'],  # 保留完整的URL在value中
                'data_type': self['data_type'],
                'task_id': self.get('task_id'),
                'worker_id': self.get('worker_id'),
                'crawl_time': self['crawl_time'],
                'fetch_type': self.get('fetch_type', 'requests'),
                'status_code': self.get('status_code', 200),
                'source_list_url': self.get('source_list_url'),
                'list_data': self.get('list_data', {}),
                'detail_data': self.get('detail_data', {}),
                'raw_data': self.get('raw_data', {})
            }
        }


class ErrorItem(scrapy.Item):
    """错误Item，用于记录爬取失败的情况"""
    url = scrapy.Field()
    data_type = scrapy.Field()
    task_id = scrapy.Field()
    worker_id = scrapy.Field()
    error_time = scrapy.Field()
    error_type = scrapy.Field()
    error_message = scrapy.Field()
    retry_count = scrapy.Field()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self['error_time'] = datetime.now().isoformat()
    
    def to_kafka_message(self) -> Dict[str, Any]:
        """
        转换为Kafka错误消息格式
        Key格式: task_id:error:url_hash
        Value包含完整的URL和错误信息
        """
        # 生成URL的短哈希（8位）
        url_hash = hashlib.md5(self['url'].encode()).hexdigest()[:8]
        # 构建key: task_id:error:url_hash
        kafka_key = f"{self.get('task_id', 'unknown')}:error:{url_hash}"
        
        return {
            'key': kafka_key,  # Kafka消息的key
            'value': {
                'url': self['url'],  # 保留完整的URL在value中
                'data_type': 'error',
                'task_id': self.get('task_id'),
                'worker_id': self.get('worker_id'),
                'error_time': self['error_time'],
                'error_type': self.get('error_type'),
                'error_message': self.get('error_message'),
                'retry_count': self.get('retry_count', 0)
            }
        }