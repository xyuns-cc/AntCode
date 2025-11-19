import scrapy
import re
import time
from spider.spider.utils.log import logger
from typing import List, Dict, Any, Optional, Union
from urllib.parse import urljoin
from .AntCodeRedisSpider import AntCodeRedisSpider
from spider.spider.utils.ctrl_redis import RedisCtrl
from spider.spider.utils.base import ScheduledRequest
from scrapy.http import Request
from spider.spider.items import ListPageItem, DetailPageItem, ErrorItem

class AntcodeScrapySpider(AntCodeRedisSpider):
    name = "antcode_scrapy"
    allowed_domains = []  # 动态设置，不限制域名
    redis_key = 'AntcodeScrapySpider:start_urls'

    # 自定义 settings，确保中间件启用
    custom_settings = {
        'CONCURRENT_REQUESTS_PER_DOMAIN': 8,
        'LOG_LEVEL': 'INFO',
        'DRISSIONPAGE_ENABLED': True,  # 启用DrissionPage中间件
        'DRISSIONPAGE_HEADLESS': True,  # 无头模式
    }
    
    def make_request_from_data(self, data):
        """
        重写父类方法，处理初始请求时的分页设置
        支持三种模式：无分页、URL模式分页、点击元素分页
        """
        request = super().make_request_from_data(data)
        if not request:
            return None
            
        # 检查是否是列表页且需要处理分页
        if request.callback.__name__ == 'parse_list':
            meta = request.meta
            pagination = meta.get('pagination', {})
            
            if pagination:  # 有分页配置
                method = pagination.get('method')
                
                if method == 'url_pattern':
                    # URL模式分页：检查URL中是否有{}占位符
                    if '{}' in request.url:
                        start_page = pagination.get('start_page', 1)
                        # 将URL中的{}替换为起始页码
                        new_url = request.url.replace('{}', str(start_page))
                        request = request.replace(url=new_url)
                        # 在meta中记录原始URL模板和当前页码
                        meta['original_url'] = request.url.replace(str(start_page), '{}')
                        meta['current_page'] = start_page
                        
                elif method == 'click_element':
                    # 点击元素分页：确保使用browser模式
                    if meta.get('fetch_type') != 'browser':
                        logger.warning(f"点击分页需要使用browser模式，当前：{meta.get('fetch_type')}")
                        meta['fetch_type'] = 'browser'
                    # 初始化页码
                    meta['current_page'] = pagination.get('start_page', 1)
                    
        return request

    def get_callback(self, callback):
        # url去重设置：True 不去重 False 去重
        callback_dt = {
            'list': (self.parse_list, True),
            'detail': (self.parse_detail, True),
        }
        return callback_dt.get(callback)


    def parse_list(self, response):
        """
        解析列表页
        支持：
        1. 提取详情页链接
        2. 提取列表页数据
        3. 处理分页（无分页、URL模式、点击元素）
        """
        reqs = []
        meta = response.meta
        rules = meta.get('rules', [])

        extracted_data = {}
        detail_urls = []
        
        # 遍历规则，提取数据
        for rule in rules:
            desc = rule.get('desc', '')
            rule_type = rule.get('type')
            expr = rule.get('expr')
            
            if not rule_type or not expr:
                continue
                
            try:
                if rule_type == 'xpath':
                    results = response.xpath(expr).getall()
                    
                elif rule_type == 'css':
                    results = response.css(expr).getall()
                    
                elif rule_type == 'regex':
                    if extracted_data:
                        results = []
                        for key, values in extracted_data.items():
                            if isinstance(values, list):
                                for value in values:
                                    matches = re.findall(expr, str(value))
                                    results.extend(matches)
                            else:
                                matches = re.findall(expr, str(values))
                                results.extend(matches)
                    else:
                        # 对整个响应文本应用正则
                        results = re.findall(expr, response.text)
                        
                else:
                    logger.warning(f"未知的规则类型: {rule_type}")
                    continue
                    
                # 根据描述判断数据用途
                if any(keyword in desc.lower() for keyword in ['详情页', 'detail', 'link', '链接']) or \
                   'href' in expr.lower() or '@href' in expr:
                    # 这是详情页链接
                    detail_urls.extend(results)
                else:
                    # 这是其他提取的数据
                    extracted_data[desc] = results
                    
            except Exception as e:
                logger.error(f"提取规则执行失败 - {desc}: {e}")
                continue
        
        # 创建并yield列表页Item
        list_item = ListPageItem()
        list_item['url'] = response.url
        list_item['task_id'] = meta.get('task_id')
        list_item['worker_id'] = meta.get('worker_id')
        list_item['fetch_type'] = meta.get('fetch_type', 'requests')
        list_item['status_code'] = response.status
        list_item['extracted_data'] = extracted_data
        list_item['detail_urls'] = detail_urls
        list_item['page_number'] = meta.get('current_page', meta.get('page_number', 1))
        list_item['raw_data'] = {
            'rules': rules,
            'pagination': meta.get('pagination', {})
        }
        
        # yield列表页数据
        yield list_item
        
        # 处理分页（支持三种模式）
        pagination = meta.get('pagination', {})
        if pagination:
            pagination_method = pagination.get('method')
            if pagination_method == 'url_pattern':
                self._handle_url_pagination(response, pagination, reqs)
            elif pagination_method == 'click_element':
                self._handle_click_pagination(response, pagination, reqs)
            else:
                logger.warning(f"未知的分页方法: {pagination_method}")
        else:
            logger.info("该任务没有分页配置")
        
        # 为每个详情页URL创建请求
        for url in detail_urls:
            # 处理相对URL
            absolute_url = urljoin(response.url, url)
            
            # 构建新的meta，传递必要的信息
            new_meta = {
                'task_id': meta.get('task_id'),
                'worker_id': meta.get('worker_id'),
                'proxy': meta.get('proxy'),
                'fetch_type': meta.get('fetch_type', 'requests'),
                'list_data': extracted_data,  # 传递从列表页提取的数据
                'source_list_url': response.url,  # 记录来源列表页URL
                'rules': meta.get('rules', [])  # 传递详情页提取规则
            }
            
            req = ScheduledRequest(
                url=absolute_url,
                method='GET',
                callback='detail',
                body={},
                meta=new_meta,
                headers=response.request.headers.to_unicode_dict(),
                cookies=response.request.cookies,
                priority=response.meta.get('priority', 0),
                dont_filter=response.meta.get('dont_filter', False)
            )
            reqs.append(req)
        
        # 将请求推送到Redis
        if reqs:
            RedisCtrl().reqs_push(self.redis_key, reqs)
            logger.info(f"从列表页提取了 {len(reqs)} 个详情页请求")

    def _handle_url_pagination(self, response, pagination, reqs):
        """处理URL模式分页"""
        current_page = response.meta.get('current_page', 1)
        max_pages = pagination.get('max_pages', 10)
        
        # 获取原始URL模板
        original_url = response.meta.get('original_url', response.url)
        
        # 检查URL中是否包含{}占位符
        if '{}' not in original_url:
            logger.warning("URL中未找到{}占位符，无法进行分页")
            return

        # 计算下一页
        next_page = current_page + 1
        
        # 检查是否超过最大页数
        if next_page > max_pages:
            logger.info(f"已达到最大页数限制: {max_pages}")
            return
            
        # 生成下一页URL
        next_url = original_url.replace('{}', str(next_page))

        # 构建下一页请求
        next_meta = response.meta.copy()
        next_meta['current_page'] = next_page
        next_meta['original_url'] = original_url

        next_req = ScheduledRequest(
            url=next_url,
            method=response.request.method,
            callback='list',
            body=response.request.body or {},
            meta=next_meta,
            headers=response.request.headers.to_unicode_dict(),
            cookies=response.request.cookies,
            priority=response.meta.get('priority', 0),
            dont_filter=response.meta.get('dont_filter', False)
        )
        reqs.append(next_req)
        logger.info(f"添加下一页请求: 第 {next_page} 页 - URL: {next_url}")
    
    def _handle_click_pagination(self, response, pagination, reqs):
        """处理点击元素分页（仅支持browser模式的GET请求）"""
        current_page = response.meta.get('current_page', 1)
        max_pages = pagination.get('max_pages', 10)
        
        # 检查是否已达到最大页数
        if current_page >= max_pages:
            logger.info(f"已达到最大页数限制: {max_pages}")
            return
        
        # 获取下一页按钮的规则
        next_page_rule = pagination.get('next_page_rule', {})
        if not next_page_rule:
            logger.error("点击分页需要提供next_page_rule配置")
            return
        
        # 构建下一页请求（保持当前URL不变，通过点击实现翻页）
        next_meta = response.meta.copy()
        next_meta['current_page'] = current_page + 1
        next_meta['fetch_type'] = 'browser'  # 确保使用browser模式
        
        # 设置DrissionPage的点击配置
        selector_type = next_page_rule.get('type', 'xpath')
        selector_expr = next_page_rule.get('expr', '')
        
        if selector_type == 'xpath':
            click_selector = selector_expr
        elif selector_type == 'css':
            # DrissionPage使用CSS选择器格式
            click_selector = f'css:{selector_expr}'
        else:
            logger.error(f"不支持的选择器类型: {selector_type}")
            return
        
        # 添加点击元素和等待时间到meta
        next_meta['click_selectors'] = [click_selector]
        wait_time_ms = pagination.get('wait_after_click_ms', 2500)
        next_meta['wait_time'] = wait_time_ms / 1000  # 转换为秒
        
        # 构建请求（URL保持不变，通过点击翻页）
        next_req = ScheduledRequest(
            url=response.url,  # 保持当前URL
            method='GET',  # 点击分页只支持GET
            callback='list',
            body={},
            meta=next_meta,
            headers=response.request.headers.to_unicode_dict(),
            cookies=response.request.cookies,
            priority=response.meta.get('priority', 0),
            dont_filter=True  # 点击分页需要允许重复URL
        )
        reqs.append(next_req)
        logger.info(f"添加点击分页请求: 第 {current_page + 1} 页")




    def parse_detail(self, response):
        """
        解析详情页
        支持从meta中的rules提取数据
        返回DetailPageItem
        """
        meta = response.meta
        list_data = meta.get('list_data', {})
        rules = meta.get('rules', [])
        
        logger.info(f"解析详情页: {response.url}")
        
        try:
            # 提取详情页数据
            detail_data = {}
            
            # 应用提取规则
            for rule in rules:
                desc = rule.get('desc', '')
                rule_type = rule.get('type')
                expr = rule.get('expr')
                
                if not rule_type or not expr:
                    continue
                
                try:
                    if rule_type == 'xpath':
                        results = response.xpath(expr).getall()
                    elif rule_type == 'css':
                        results = response.css(expr).getall()
                    elif rule_type == 'regex':
                        results = re.findall(expr, response.text)
                    else:
                        logger.warning(f"未知的规则类型: {rule_type}")
                        continue
                    
                    # 保存提取的数据
                    detail_data[desc] = results
                    
                except Exception as e:
                    logger.error(f"详情页规则执行失败 - {desc}: {e}")
                    continue
            
            # 创建并yield详情页Item
            detail_item = DetailPageItem()
            detail_item['url'] = response.url
            detail_item['task_id'] = meta.get('task_id')
            detail_item['worker_id'] = meta.get('worker_id')
            detail_item['fetch_type'] = meta.get('fetch_type', 'requests')
            detail_item['status_code'] = response.status
            detail_item['source_list_url'] = meta.get('source_list_url')
            detail_item['list_data'] = list_data
            detail_item['detail_data'] = detail_data
            detail_item['raw_data'] = {'rules': rules}
            
            logger.info(f"详情页数据提取完成: {response.url}")
            
            # yield详情页数据
            yield detail_item
            
        except Exception as e:
            # 如果出现错误，yield ErrorItem
            logger.error(f"解析详情页失败: {response.url}, 错误: {e}")
            
            error_item = ErrorItem()
            error_item['url'] = response.url
            error_item['data_type'] = 'detail'
            error_item['task_id'] = meta.get('task_id')
            error_item['worker_id'] = meta.get('worker_id')
            error_item['error_type'] = type(e).__name__
            error_item['error_message'] = str(e)
            error_item['retry_count'] = meta.get('retry_count', 0)
            
            yield error_item
