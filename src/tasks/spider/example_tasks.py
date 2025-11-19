#!/usr/bin/env python3
"""
示例任务格式文档
展示三种支持的任务JSON格式：
1. 无分页模式
2. URL模式分页
3. 点击元素分页
"""

import json
import redis
from spider.spider.utils.ctrl_redis import RedisCtrl

# Redis连接配置
REDIS_URL = 'redis://:sisui123..@127.0.0.1:6379'
REDIS_KEY = 'AntcodeScrapySpider:start_urls'


def create_task_without_pagination():
    """
    创建无分页任务示例
    """
    task = {
        "url": "https://example.com/books",
        "callback": "list",
        "method": "GET",
        "meta": {
            "fetch_type": "requests",  # 使用标准requests方式
            # 没有pagination字段，表示不进行分页
            "rules": [
                {
                    "desc": "提取图书详情页链接",
                    "type": "xpath",
                    "expr": "//div[@class='book-item']/a/@href"
                },
                {
                    "desc": "提取图书标题",
                    "type": "css",
                    "expr": "div.book-item h2.title::text"
                },
                {
                    "desc": "提取发布时间",
                    "type": "xpath",
                    "expr": "//div[@class='book-item']//span[@class='publish-date']/text()"
                },
                {
                    "desc": "从时间文本中提取日期",
                    "type": "regex",
                    "expr": r"(\d{4}-\d{2}-\d{2})"
                },
                {
                    "desc": "提取作者姓名",
                    "type": "css",
                    "expr": "div.book-item > div.author::text"
                }
            ],
            "proxy": "http://user:password@proxy.example.com:8080",
            "task_id": "antcode-20241206120000123-abc1234",
            "worker_id": "Scraper-Node-US-East-01"
        },
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        },
        "cookies": {},
        "priority": 0,
        "dont_filter": False
    }
    return task


def create_task_with_url_pagination():
    """
    创建URL模式分页任务示例
    URL中包含{}占位符，用于替换页码
    """
    task = {
        "url": "https://example.com/books?page={}",  # {}将被替换为页码
        "callback": "list",
        "method": "GET",
        "meta": {
            "fetch_type": "requests",
            "pagination": {
                "method": "url_pattern",  # URL模式分页
                "start_page": 1,  # 起始页码
                "max_pages": 10   # 最大页数
            },
            "rules": [
                {
                    "desc": "提取图书详情页链接",
                    "type": "xpath",
                    "expr": "//div[@class='book-item']/a/@href"
                },
                {
                    "desc": "提取图书标题",
                    "type": "css",
                    "expr": "div.book-item h2.title::text"
                },
                {
                    "desc": "提取价格",
                    "type": "xpath",
                    "expr": "//span[@class='price']/text()"
                },
                {
                    "desc": "提取评分",
                    "type": "css",
                    "expr": "div.rating span.score::text"
                }
            ],
            "proxy": "socks5://user:pass@proxy.example.com:1080",  # 支持SOCKS代理
            "task_id": "antcode-20241206120100456-def5678",
            "worker_id": "Scraper-Node-EU-West-01"
        },
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept": "text/html,application/xhtml+xml"
        },
        "cookies": {
            "session_id": "abc123xyz"
        },
        "priority": 1,
        "dont_filter": False
    }
    return task


def create_task_with_click_pagination():
    """
    创建点击元素分页任务示例
    使用浏览器模式，通过点击"下一页"按钮实现分页
    注意：此模式仅支持GET请求
    """
    task = {
        "url": "https://example.com/dynamic-books",
        "callback": "list",
        "method": "GET",  # 点击分页仅支持GET
        "meta": {
            "fetch_type": "browser",  # 必须使用browser模式
            "pagination": {
                "method": "click_element",  # 点击元素分页
                "start_page": 1,
                "max_pages": 5,
                "next_page_rule": {
                    "desc": "定位下一页按钮",
                    "type": "xpath",
                    "expr": "//a[@class='next-page' and not(contains(@class,'disabled'))]"
                },
                "wait_after_click_ms": 2500  # 点击后等待时间（毫秒）
            },
            "rules": [
                {
                    "desc": "提取动态加载的详情页链接",
                    "type": "xpath",
                    "expr": "//div[@data-book-id]/a/@href"
                },
                {
                    "desc": "提取书名",
                    "type": "css",
                    "expr": "[data-book-title]::text"
                },
                {
                    "desc": "提取库存状态",
                    "type": "xpath",
                    "expr": "//span[contains(@class,'stock-status')]/text()"
                }
            ],
            "wait_time": 3,  # 页面初始加载等待时间（秒）
            "scroll_to_bottom": True,  # 滚动到页面底部
            "proxy": "http://proxy.example.com:8080",
            "task_id": "antcode-20241206120200789-ghi9012",
            "worker_id": "Scraper-Node-AS-Pacific-01"
        },
        "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        },
        "cookies": {},
        "priority": 2,
        "dont_filter": True  # 点击分页需要允许重复URL
    }
    return task


def create_detail_page_task():
    """
    创建详情页任务示例
    通常由列表页生成，用于提取详细信息
    """
    task = {
        "url": "https://example.com/book/12345",
        "callback": "detail",  # 详情页回调
        "method": "GET",
        "meta": {
            "fetch_type": "requests",
            "rules": [
                {
                    "desc": "提取书名",
                    "type": "xpath",
                    "expr": "//h1[@class='book-title']/text()"
                },
                {
                    "desc": "提取作者",
                    "type": "css",
                    "expr": "div.author-info span.name::text"
                },
                {
                    "desc": "提取ISBN",
                    "type": "regex",
                    "expr": r"ISBN[:\s]*(\d{10,13})"
                },
                {
                    "desc": "提取价格",
                    "type": "xpath",
                    "expr": "//div[@class='price-box']//span[@class='current-price']/text()"
                },
                {
                    "desc": "提取描述",
                    "type": "css",
                    "expr": "div.book-description p::text"
                }
            ],
            "list_data": {  # 从列表页传递的数据
                "category": "Science Fiction",
                "list_position": 5
            },
            "proxy": "http://proxy.example.com:8080",
            "task_id": "antcode-20241206120300345-jkl3456",
            "worker_id": "Scraper-Node-US-East-01"
        },
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://example.com/books"
        },
        "cookies": {},
        "priority": 5,  # 详情页通常优先级更高
        "dont_filter": False
    }
    return task


def push_task_to_redis(task, redis_client=None):
    """
    将任务推送到Redis队列
    
    :param task: 任务字典
    :param redis_client: Redis客户端（可选）
    """
    if not redis_client:
        # 使用项目中的RedisCtrl
        redis_ctrl = RedisCtrl()
        redis_ctrl.req_push(REDIS_KEY, task)
        print(f"任务已推送到Redis队列: {REDIS_KEY}")
    else:
        # 使用提供的Redis客户端
        task_json = json.dumps(task, ensure_ascii=False)
        redis_client.lpush(REDIS_KEY, task_json)
        print(f"任务已推送到Redis队列: {REDIS_KEY}")


def main():
    """
    主函数：演示如何创建和推送不同类型的任务
    """
    print("爬虫任务示例\n" + "="*50)
    
    # 1. 无分页任务
    print("\n1. 无分页任务示例:")
    task1 = create_task_without_pagination()
    print(json.dumps(task1, indent=2, ensure_ascii=False))
    
    # 2. URL模式分页任务
    print("\n2. URL模式分页任务示例:")
    task2 = create_task_with_url_pagination()
    print(json.dumps(task2, indent=2, ensure_ascii=False))
    
    # 3. 点击元素分页任务
    print("\n3. 点击元素分页任务示例:")
    task3 = create_task_with_click_pagination()
    print(json.dumps(task3, indent=2, ensure_ascii=False))
    
    # 4. 详情页任务
    print("\n4. 详情页任务示例:")
    task4 = create_detail_page_task()
    print(json.dumps(task4, indent=2, ensure_ascii=False))
    
    # 推送任务到Redis（需要取消注释）
    # push_task_to_redis(task1)
    # push_task_to_redis(task2)
    # push_task_to_redis(task3)
    # push_task_to_redis(task4)
    
    print("\n" + "="*50)
    print("提示：取消注释push_task_to_redis()调用以将任务推送到Redis")


if __name__ == "__main__":
    main()