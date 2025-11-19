"""
Kafka Pipeline
将爬取的数据发送到Kafka队列等待清洗
支持列表页和详情页两种数据类型
"""

import json
from typing import Dict, Any, Optional
from kafka import KafkaProducer
from kafka.errors import KafkaError
from scrapy import Spider
from scrapy.exceptions import DropItem
from spider.spider.items import ListPageItem, DetailPageItem, ErrorItem
from spider.spider.utils.log import logger


class KafkaPipeline:
    """
    Kafka数据管道
    将爬取的数据发送到Kafka，以URL为键
    """
    
    def __init__(self, kafka_settings: Dict[str, Any]):
        """
        初始化Kafka Pipeline
        
        :param kafka_settings: Kafka配置字典
        """
        self.kafka_settings = kafka_settings
        self.producer = None
        self.topic_list = kafka_settings.get('TOPIC_LIST', 'spider_list_data')
        self.topic_detail = kafka_settings.get('TOPIC_DETAIL', 'spider_detail_data')
        self.topic_error = kafka_settings.get('TOPIC_ERROR', 'spider_error_data')
        self.batch_size = kafka_settings.get('BATCH_SIZE', 100)
        self.batch_timeout = kafka_settings.get('BATCH_TIMEOUT', 10)
        
        # 统计信息
        self.stats = {
            'list_sent': 0,
            'detail_sent': 0,
            'error_sent': 0,
            'failed': 0
        }
    
    @classmethod
    def from_crawler(cls, crawler):
        """
        从crawler获取配置并创建实例
        """
        kafka_settings = {
            'BOOTSTRAP_SERVERS': crawler.settings.get('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092'),
            'TOPIC_LIST': crawler.settings.get('KAFKA_TOPIC_LIST', 'spider_list_data'),
            'TOPIC_DETAIL': crawler.settings.get('KAFKA_TOPIC_DETAIL', 'spider_detail_data'),
            'TOPIC_ERROR': crawler.settings.get('KAFKA_TOPIC_ERROR', 'spider_error_data'),
            'BATCH_SIZE': crawler.settings.getint('KAFKA_BATCH_SIZE', 100),
            'BATCH_TIMEOUT': crawler.settings.getint('KAFKA_BATCH_TIMEOUT', 10),
            'COMPRESSION_TYPE': crawler.settings.get('KAFKA_COMPRESSION_TYPE', 'gzip'),
            'RETRIES': crawler.settings.getint('KAFKA_RETRIES', 3),
            'MAX_IN_FLIGHT_REQUESTS': crawler.settings.getint('KAFKA_MAX_IN_FLIGHT_REQUESTS', 5),
            'ACKS': crawler.settings.get('KAFKA_ACKS', 'all'),
            'CLIENT_ID': crawler.settings.get('KAFKA_CLIENT_ID', 'scrapy-spider'),
            'SECURITY_PROTOCOL': crawler.settings.get('KAFKA_SECURITY_PROTOCOL', 'PLAINTEXT'),
            'SASL_MECHANISM': crawler.settings.get('KAFKA_SASL_MECHANISM'),
            'SASL_USERNAME': crawler.settings.get('KAFKA_SASL_USERNAME'),
            'SASL_PASSWORD': crawler.settings.get('KAFKA_SASL_PASSWORD'),
        }
        return cls(kafka_settings)
    
    def open_spider(self, spider: Spider):
        """
        爬虫启动时初始化Kafka连接
        """
        try:
            # 配置Kafka生产者
            producer_config = {
                'bootstrap_servers': self.kafka_settings['BOOTSTRAP_SERVERS'],
                'compression_type': self.kafka_settings['COMPRESSION_TYPE'],
                'retries': self.kafka_settings['RETRIES'],
                'max_in_flight_requests_per_connection': self.kafka_settings['MAX_IN_FLIGHT_REQUESTS'],
                'acks': self.kafka_settings['ACKS'],
                'client_id': self.kafka_settings['CLIENT_ID'],
                'value_serializer': lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),
                'key_serializer': lambda k: k.encode('utf-8') if k else None,
                'batch_size': self.batch_size * 1024,  # 转换为字节
                'linger_ms': self.batch_timeout * 1000,  # 转换为毫秒
            }
            
            # 添加认证配置（如果需要）
            if self.kafka_settings.get('SECURITY_PROTOCOL') != 'PLAINTEXT':
                producer_config['security_protocol'] = self.kafka_settings['SECURITY_PROTOCOL']
                if self.kafka_settings.get('SASL_MECHANISM'):
                    producer_config['sasl_mechanism'] = self.kafka_settings['SASL_MECHANISM']
                    producer_config['sasl_plain_username'] = self.kafka_settings['SASL_USERNAME']
                    producer_config['sasl_plain_password'] = self.kafka_settings['SASL_PASSWORD']
            
            self.producer = KafkaProducer(**producer_config)
            logger.info(f"Kafka producer initialized. Servers: {self.kafka_settings['BOOTSTRAP_SERVERS']}")
            
        except Exception as e:
            logger.error(f"Failed to initialize Kafka producer: {e}")
            raise
    
    def close_spider(self, spider: Spider):
        """
        爬虫关闭时清理资源
        """
        if self.producer:
            try:
                # 确保所有消息都被发送
                self.producer.flush()
                self.producer.close()
                logger.info(f"Kafka producer closed. Stats: {self.stats}")
            except Exception as e:
                logger.error(f"Error closing Kafka producer: {e}")
    
    def process_item(self, item, spider: Spider):
        """
        处理Item，发送到相应的Kafka主题
        
        :param item: 爬取的Item
        :param spider: 爬虫实例
        :return: 处理后的Item
        """
        if not self.producer:
            logger.error("Kafka producer not initialized")
            raise DropItem("Kafka producer not initialized")
        
        try:
            # 根据Item类型选择主题和处理方式
            if isinstance(item, ListPageItem):
                topic = self.topic_list
                message = item.to_kafka_message()
                self._send_to_kafka(topic, message['key'], message['value'])
                self.stats['list_sent'] += 1
                logger.debug(f"Sent list page data to Kafka: {item['url']}")
                
            elif isinstance(item, DetailPageItem):
                topic = self.topic_detail
                message = item.to_kafka_message()
                self._send_to_kafka(topic, message['key'], message['value'])
                self.stats['detail_sent'] += 1
                logger.debug(f"Sent detail page data to Kafka: {item['url']}")
                
            elif isinstance(item, ErrorItem):
                topic = self.topic_error
                message = item.to_kafka_message()
                self._send_to_kafka(topic, message['key'], message['value'])
                self.stats['error_sent'] += 1
                logger.debug(f"Sent error data to Kafka: {item['url']}")
                
            else:
                # 对于未知类型的Item，尝试发送到默认主题
                if hasattr(item, 'get') and item.get('url'):
                    topic = self.topic_list if item.get('data_type') == 'list' else self.topic_detail
                    self._send_to_kafka(topic, item['url'], dict(item))
                    logger.warning(f"Sent unknown item type to Kafka: {item.get('url')}")
                else:
                    raise DropItem(f"Unknown item type: {type(item)}")
            
            return item
            
        except KafkaError as e:
            self.stats['failed'] += 1
            logger.error(f"Kafka error processing item: {e}")
            # 可以选择是否丢弃Item或重试
            # raise DropItem(f"Kafka error: {e}")
            return item  # 继续传递给其他Pipeline
            
        except Exception as e:
            self.stats['failed'] += 1
            logger.error(f"Error processing item: {e}")
            return item
    
    def _send_to_kafka(self, topic: str, key: Optional[str], value: Dict[str, Any]):
        """
        发送消息到Kafka
        
        :param topic: Kafka主题
        :param key: 消息键（URL）
        :param value: 消息值（数据）
        """
        try:
            # 异步发送消息
            future = self.producer.send(
                topic=topic,
                key=key,
                value=value
            )
            
            # 可选：等待发送完成（同步模式）
            # 注意：这会降低性能，但能立即获知发送结果
            # record_metadata = future.get(timeout=10)
            # logger.debug(f"Message sent to {record_metadata.topic} partition {record_metadata.partition}")
            
            # 注册回调（异步模式）
            future.add_callback(self._on_send_success, topic, key)
            future.add_errback(self._on_send_error, topic, key)
            
        except Exception as e:
            logger.error(f"Failed to send message to Kafka: {e}")
            raise
    
    def _on_send_success(self, record_metadata, topic: str, key: str):
        """
        发送成功回调
        """
        logger.debug(
            f"Message sent successfully to {topic} "
            f"partition {record_metadata.partition} "
            f"offset {record_metadata.offset} "
            f"key: {key}"
        )
    
    def _on_send_error(self, exception, topic: str, key: str):
        """
        发送失败回调
        """
        logger.error(f"Failed to send message to {topic} key: {key}, error: {exception}")
        self.stats['failed'] += 1


class BufferedKafkaPipeline(KafkaPipeline):
    """
    带缓冲的Kafka Pipeline
    批量发送数据以提高性能
    """
    
    def __init__(self, kafka_settings: Dict[str, Any]):
        super().__init__(kafka_settings)
        self.buffer = {
            self.topic_list: [],
            self.topic_detail: [],
            self.topic_error: []
        }
        self.buffer_size = kafka_settings.get('BUFFER_SIZE', 100)
    
    def process_item(self, item, spider: Spider):
        """
        处理Item，添加到缓冲区
        """
        if not self.producer:
            logger.error("Kafka producer not initialized")
            raise DropItem("Kafka producer not initialized")
        
        try:
            # 根据Item类型添加到相应缓冲区
            if isinstance(item, ListPageItem):
                message = item.to_kafka_message()
                self.buffer[self.topic_list].append(message)
                
            elif isinstance(item, DetailPageItem):
                message = item.to_kafka_message()
                self.buffer[self.topic_detail].append(message)
                
            elif isinstance(item, ErrorItem):
                message = item.to_kafka_message()
                self.buffer[self.topic_error].append(message)
            
            # 检查是否需要刷新缓冲区
            self._check_and_flush_buffers()
            
            return item
            
        except Exception as e:
            logger.error(f"Error processing item: {e}")
            return item
    
    def _check_and_flush_buffers(self):
        """
        检查并刷新缓冲区
        """
        for topic, buffer in self.buffer.items():
            if len(buffer) >= self.buffer_size:
                self._flush_buffer(topic, buffer)
                self.buffer[topic] = []
    
    def _flush_buffer(self, topic: str, buffer: list):
        """
        批量发送缓冲区数据
        """
        for message in buffer:
            self._send_to_kafka(topic, message['key'], message['value'])
        logger.info(f"Flushed {len(buffer)} messages to {topic}")
    
    def close_spider(self, spider: Spider):
        """
        爬虫关闭时刷新所有缓冲区
        """
        # 刷新所有缓冲区
        for topic, buffer in self.buffer.items():
            if buffer:
                self._flush_buffer(topic, buffer)
        
        # 调用父类方法关闭producer
        super().close_spider(spider)