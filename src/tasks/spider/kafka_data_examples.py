#!/usr/bin/env python3
"""
Kafka数据格式示例
展示发送到Kafka的列表页和详情页数据结构
"""

import json
from datetime import datetime

# ============================================
# 1. 列表页数据示例 (Topic: spider_list_data)
# ============================================

list_page_example = {
    # Kafka消息的Key（字符串）- 格式: task_id:data_type:url_hash
    "key": "antcode-20241206153045123-abc1234:list:a3f5d8c9",
    
    # Kafka消息的Value（JSON）
    "value": {
        # 基础信息
        "url": "https://example.com/books?page=1",
        "data_type": "list",  # 数据类型标识
        "crawl_time": "2024-12-06T15:30:45.123456",  # ISO格式时间戳
        
        # 任务信息
        "task_id": "antcode-20241206153045123-abc1234",
        "worker_id": "Scraper-Node-US-East-01",
        
        # 爬取信息
        "fetch_type": "requests",  # 爬取方式: requests/browser/curl_cffi
        "status_code": 200,
        "page_number": 1,  # 当前页码（如果有分页）
        
        # 从列表页提取的结构化数据
        "extracted_data": {
            "提取图书标题": [
                "Python编程：从入门到实践",
                "流畅的Python",
                "Python Cookbook",
                "深入理解Python特性"
            ],
            "提取发布时间": [
                "2024-01-15",
                "2024-02-20", 
                "2024-03-10",
                "2024-04-05"
            ],
            "提取作者姓名": [
                "Eric Matthes",
                "Luciano Ramalho",
                "David Beazley",
                "Dan Bader"
            ],
            "提取价格": [
                "¥89.00",
                "¥139.00",
                "¥108.00",
                "¥79.00"
            ]
        },
        
        # 提取到的详情页URL列表
        "detail_urls": [
            "https://example.com/book/12345",
            "https://example.com/book/12346",
            "https://example.com/book/12347",
            "https://example.com/book/12348"
        ],
        
        # 统计信息
        "total_items": 4,  # 本页提取的条目数
        
        # 原始配置数据（用于调试和数据溯源）
        "raw_data": {
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
                    "desc": "提取作者姓名",
                    "type": "css",
                    "expr": "div.book-item > div.author::text"
                },
                {
                    "desc": "提取价格",
                    "type": "xpath",
                    "expr": "//span[@class='price']/text()"
                }
            ],
            "pagination": {
                "method": "url_pattern",
                "start_page": 1,
                "max_pages": 10
            }
        }
    }
}

# ============================================
# 2. 详情页数据示例 (Topic: spider_detail_data)
# ============================================

detail_page_example = {
    # Kafka消息的Key（字符串）- 格式: task_id:data_type:url_hash
    "key": "antcode-20241206153045123-abc1234:detail:b7e2f4a1",
    
    # Kafka消息的Value（JSON）
    "value": {
        # 基础信息
        "url": "https://example.com/book/12345",
        "data_type": "detail",  # 数据类型标识
        "crawl_time": "2024-12-06T15:31:20.456789",
        
        # 任务信息
        "task_id": "antcode-20241206153045123-abc1234",
        "worker_id": "Scraper-Node-US-East-01",
        
        # 爬取信息
        "fetch_type": "requests",
        "status_code": 200,
        
        # 来源信息
        "source_list_url": "https://example.com/books?page=1",  # 来源列表页URL
        
        # 从列表页传递过来的数据
        "list_data": {
            "提取图书标题": ["Python编程：从入门到实践"],
            "提取发布时间": ["2024-01-15"],
            "提取作者姓名": ["Eric Matthes"],
            "提取价格": ["¥89.00"]
        },
        
        # 从详情页提取的详细数据
        "detail_data": {
            "书名": ["Python编程：从入门到实践（第3版）"],
            "作者": ["[美] Eric Matthes"],
            "译者": ["袁国忠"],
            "ISBN": ["9787115606010"],
            "出版社": ["人民邮电出版社"],
            "出版时间": ["2024年1月"],
            "页数": ["456"],
            "定价": ["¥89.00"],
            "装帧": ["平装"],
            "丛书": ["图灵程序设计丛书"],
            "评分": ["9.2"],
            "评价人数": ["2341"],
            "内容简介": [
                "本书是针对所有层次Python读者而作的Python入门书。全书分两部分：第一部分介绍用Python编程所必须了解的基本概念，包括Matplotlib、NumPy和Pygal等强大的Python库和工具，以及列表、字典、if语句、类、文件与异常、代码测试等内容；第二部分将理论付诸实践，讲解如何开发三个项目，包括简单的2D游戏、利用数据生成交互式的信息图以及创建和定制简单的Web应用，并帮助读者解决常见编程问题和困惑。"
            ],
            "作者简介": [
                "Eric Matthes，高中科学和数学老师，现居住在阿拉斯加，在当地讲授Python入门课程。他从5岁开始就一直在编程，并且他还是资深软件开发人员和技术图书作者。"
            ],
            "目录": [
                "第一部分 基础知识",
                "第1章 起步",
                "第2章 变量和简单数据类型",
                "第3章 列表简介",
                "第4章 操作列表",
                "第5章 if语句",
                "..."
            ],
            "标签": ["Python", "编程", "入门", "计算机"],
            "推荐语": [
                "Python入门经典，销量超百万册",
                "零基础自学Python首选"
            ],
            "库存状态": ["有货"],
            "配送信息": ["支持7天无理由退货"],
            "商品链接": ["https://example.com/book/12345"],
            "图片链接": [
                "https://example.com/images/book12345_cover.jpg",
                "https://example.com/images/book12345_back.jpg"
            ]
        },
        
        # 原始配置数据
        "raw_data": {
            "rules": [
                {
                    "desc": "书名",
                    "type": "xpath",
                    "expr": "//h1[@class='book-title']/text()"
                },
                {
                    "desc": "作者",
                    "type": "css",
                    "expr": "div.author-info span.name::text"
                },
                {
                    "desc": "ISBN",
                    "type": "regex",
                    "expr": r"ISBN[:\s]*(\d{10,13})"
                },
                {
                    "desc": "出版社",
                    "type": "xpath",
                    "expr": "//div[@class='publisher']//a/text()"
                },
                {
                    "desc": "定价",
                    "type": "xpath",
                    "expr": "//div[@class='price-box']//span[@class='current-price']/text()"
                },
                {
                    "desc": "内容简介",
                    "type": "css",
                    "expr": "div.book-description p::text"
                }
            ]
        }
    }
}

# ============================================
# 3. 错误数据示例 (Topic: spider_error_data)
# ============================================

error_data_example = {
    # Kafka消息的Key（字符串）- 格式: task_id:data_type:url_hash
    "key": "antcode-20241206153045123-abc1234:error:c9f3a5b2",
    
    # Kafka消息的Value（JSON）
    "value": {
        "url": "https://example.com/book/99999",
        "data_type": "error",
        "error_time": "2024-12-06T15:32:10.789012",
        "task_id": "antcode-20241206153045123-abc1234",
        "worker_id": "Scraper-Node-US-East-01",
        "error_type": "HTTPError",
        "error_message": "404 Client Error: Not Found for url: https://example.com/book/99999",
        "retry_count": 3
    }
}

# ============================================
# 4. 带分页的列表页数据示例
# ============================================

list_page_with_pagination = {
    "key": "antcode-20241206153045123-def5678:list:d5e7c3a8",
    
    "value": {
        "url": "https://example.com/books?page=5",
        "data_type": "list",
        "crawl_time": "2024-12-06T15:35:30.123456",
        "task_id": "antcode-20241206153045123-def5678",
        "worker_id": "Scraper-Node-EU-West-01",
        "fetch_type": "browser",  # 使用浏览器渲染
        "status_code": 200,
        "page_number": 5,  # 第5页
        
        "extracted_data": {
            "提取图书标题": [
                "算法导论",
                "深入理解计算机系统",
                "编程珠玑"
            ],
            "提取价格": [
                "¥128.00",
                "¥139.00",
                "¥69.00"
            ]
        },
        
        "detail_urls": [
            "https://example.com/book/12380",
            "https://example.com/book/12381",
            "https://example.com/book/12382"
        ],
        
        "total_items": 3,
        
        "raw_data": {
            "rules": [...],
            "pagination": {
                "method": "click_element",  # 点击翻页
                "start_page": 1,
                "max_pages": 10,
                "current_page": 5,
                "next_page_rule": {
                    "type": "xpath",
                    "expr": "//a[@class='next-page']"
                },
                "wait_after_click_ms": 2500
            }
        }
    }
}

def print_examples():
    """打印示例数据"""
    print("=" * 80)
    print("Kafka数据格式示例")
    print("=" * 80)
    
    print("\n1. 列表页数据 (Topic: spider_list_data)")
    print("-" * 40)
    print(f"Key: {list_page_example['key']}")
    print(f"Value: {json.dumps(list_page_example['value'], indent=2, ensure_ascii=False)}")
    
    print("\n" + "=" * 80)
    print("2. 详情页数据 (Topic: spider_detail_data)")
    print("-" * 40)
    print(f"Key: {detail_page_example['key']}")
    print(f"Value: {json.dumps(detail_page_example['value'], indent=2, ensure_ascii=False)}")
    
    print("\n" + "=" * 80)
    print("3. 错误数据 (Topic: spider_error_data)")
    print("-" * 40)
    print(f"Key: {error_data_example['key']}")
    print(f"Value: {json.dumps(error_data_example['value'], indent=2, ensure_ascii=False)}")
    
    print("\n" + "=" * 80)
    print("数据特点说明：")
    print("-" * 40)
    print("""
    1. Kafka Key设计（task_id:data_type:url_hash）：
       - task_id: 任务ID，保证同一任务数据在相近分区
       - data_type: 数据类型（list/detail/error），区分不同类型
       - url_hash: URL的MD5哈希前8位，保证分区均匀
    
    2. Value包含完整的爬取数据：
       - URL: 完整的URL保存在value中，便于数据处理
       - 基础信息（类型、时间戳）
       - 任务追踪（task_id、worker_id）
       - 爬取状态（fetch_type、status_code）
       - 提取的数据（extracted_data/detail_data）
       - 原始规则（用于数据溯源和调试）
    
    3. 列表页数据特点：
       - 包含批量提取的数据（数组形式）
       - 包含详情页URL列表
       - 记录分页信息
    
    4. 详情页数据特点：
       - 包含从列表页传递的数据（list_data）
       - 包含详细的结构化数据（detail_data）
       - 记录来源列表页URL
    
    5. 数据清洗建议：
       - 根据data_type分流处理
       - 使用task_id关联同一任务的数据
       - 根据crawl_time进行时序分析
       - 使用raw_data进行数据验证
       - 通过URL字段进行数据去重和关联
    
    6. Key设计优势：
       - 负载均衡: 哈希值保证数据均匀分布到Kafka分区
       - 数据关联: 同一task_id的数据在相近分区，便于批量处理
       - 类型区分: 通过data_type快速识别数据类型
       - 无冲突: 不同URL不会产生相同的key
    """)

if __name__ == "__main__":
    print_examples()