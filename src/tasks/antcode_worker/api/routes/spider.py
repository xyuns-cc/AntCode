"""爬虫任务路由 - 与主控 API 风格保持一致"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field
from loguru import logger

from ...spider import (
    Spider,
    Request,
    HttpClient,
    ClientConfig,
)

router = APIRouter(prefix="/spider", tags=["爬虫任务"])


# ============ 请求模型 ============

class CrawlRequest(BaseModel):
    """爬取请求"""
    url: str = Field(..., description="目标 URL")
    method: str = Field("GET", description="请求方法")
    headers: Dict[str, str] = Field(default_factory=dict, description="请求头")
    cookies: Dict[str, str] = Field(default_factory=dict, description="Cookies")
    params: Dict[str, str] = Field(default_factory=dict, description="查询参数")
    data: Optional[Dict[str, Any]] = Field(None, description="POST 数据")

    # 解析规则
    xpath_rules: Dict[str, str] = Field(default_factory=dict, description="XPath 规则")
    css_rules: Dict[str, str] = Field(default_factory=dict, description="CSS 规则")
    regex_rules: Dict[str, str] = Field(default_factory=dict, description="正则规则")

    # 配置
    timeout: float = Field(30.0, description="超时时间")
    proxy: Optional[str] = Field(None, description="代理")
    impersonate: Optional[str] = Field(None, description="浏览器指纹")

    # 中间件
    use_random_ua: bool = Field(True, description="随机 UA")
    use_rate_limit: bool = Field(False, description="限速")
    rate_limit: float = Field(10.0, description="每秒请求数")


class BatchCrawlRequest(BaseModel):
    """批量爬取请求"""
    urls: List[str] = Field(..., description="URL 列表")
    xpath_rules: Dict[str, str] = Field(default_factory=dict)
    css_rules: Dict[str, str] = Field(default_factory=dict)
    regex_rules: Dict[str, str] = Field(default_factory=dict)

    concurrent: int = Field(5, description="并发数")
    timeout: float = Field(30.0)
    proxy: Optional[str] = None
    impersonate: Optional[str] = None
    use_random_ua: bool = True
    download_delay: float = Field(0, description="下载延迟")


class SpiderTaskRequest(BaseModel):
    """爬虫任务请求"""
    spider_code: str = Field(..., description="爬虫代码")
    start_urls: List[str] = Field(default_factory=list, description="起始 URL")
    settings: Dict[str, Any] = Field(default_factory=dict, description="配置")


# ============ 响应模型 ============

class CrawlResult(BaseModel):
    """爬取结果"""
    url: str
    status: int
    elapsed_ms: float
    data: Dict[str, Any]


class TaskSubmitResponse(BaseModel):
    """任务提交响应"""
    task_id: str
    total: int


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    status: str
    total: int
    completed: int
    results: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]


# ============ 全局状态 ============

_crawl_tasks: Dict[str, Dict[str, Any]] = {}
_http_client: Optional[HttpClient] = None


async def get_client() -> HttpClient:
    """获取 HTTP 客户端"""
    global _http_client
    if _http_client is None:
        _http_client = HttpClient(ClientConfig(
            impersonate="chrome110",
            rotate_impersonate=True,
        ))
        await _http_client.start()
    return _http_client


# ============ 路由 ============

@router.post("/crawl", response_model=CrawlResult)
async def crawl_single(request: CrawlRequest):
    """
    单页爬取
    
    支持 XPath、CSS、正则解析
    """
    client = await get_client()

    # 构建请求
    req = Request(
        url=request.url,
        method=request.method,
        headers=request.headers,
        cookies=request.cookies,
        params=request.params,
        data=request.data,
        timeout=request.timeout,
        proxy=request.proxy,
        impersonate=request.impersonate,
    )

    # 添加随机 UA
    if request.use_random_ua and "User-Agent" not in req.headers:
        import random
        from ...engine.spider.middlewares import USER_AGENTS
        req.headers["User-Agent"] = random.choice(USER_AGENTS)

    # 发送请求
    response = await client.fetch(req)

    if not response.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"请求失败: HTTP {response.status}"
        )

    # 解析数据
    data = {}

    # XPath 解析
    for name, xpath in request.xpath_rules.items():
        try:
            values = response.xpath(xpath).getall()
            data[name] = values[0] if len(values) == 1 else values
        except Exception as e:
            data[name] = f"解析错误: {e}"

    # CSS 解析
    for name, css in request.css_rules.items():
        try:
            values = response.css(css).getall()
            data[name] = values[0] if len(values) == 1 else values
        except Exception as e:
            data[name] = f"解析错误: {e}"

    # 正则解析
    for name, pattern in request.regex_rules.items():
        try:
            values = response.re(pattern)
            data[name] = values[0] if len(values) == 1 else values
        except Exception as e:
            data[name] = f"解析错误: {e}"

    return CrawlResult(
        url=response.url,
        status=response.status,
        elapsed_ms=response.elapsed_ms,
        data=data,
    )


@router.post("/crawl/batch", response_model=TaskSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def crawl_batch(request: BatchCrawlRequest, background_tasks: BackgroundTasks):
    """
    批量爬取（异步）
    
    返回任务 ID，通过 /spider/task/{task_id} 查询结果
    """
    task_id = str(uuid.uuid4())[:8]

    _crawl_tasks[task_id] = {
        "status": "pending",
        "total": len(request.urls),
        "completed": 0,
        "results": [],
        "errors": [],
    }

    async def do_batch_crawl():
        client = await get_client()
        task = _crawl_tasks[task_id]
        task["status"] = "running"

        semaphore = asyncio.Semaphore(request.concurrent)

        async def crawl_one(url: str):
            async with semaphore:
                try:
                    req = Request(
                        url=url,
                        timeout=request.timeout,
                        proxy=request.proxy,
                        impersonate=request.impersonate,
                    )

                    if request.use_random_ua:
                        import random
                        from ...engine.spider.middlewares import USER_AGENTS
                        req.headers["User-Agent"] = random.choice(USER_AGENTS)

                    response = await client.fetch(req)

                    data = {}
                    for name, xpath in request.xpath_rules.items():
                        data[name] = response.xpath(xpath).getall()
                    for name, css in request.css_rules.items():
                        data[name] = response.css(css).getall()
                    for name, pattern in request.regex_rules.items():
                        data[name] = response.re(pattern)

                    task["results"].append({
                        "url": url,
                        "status": response.status,
                        "data": data,
                    })
                except Exception as e:
                    task["errors"].append({"url": url, "error": str(e)})
                finally:
                    task["completed"] += 1

                    if request.download_delay > 0:
                        await asyncio.sleep(request.download_delay)

        await asyncio.gather(*[crawl_one(url) for url in request.urls])
        task["status"] = "completed"

    background_tasks.add_task(do_batch_crawl)

    return TaskSubmitResponse(task_id=task_id, total=len(request.urls))


@router.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_crawl_task(task_id: str):
    """获取爬取任务状态"""
    if task_id not in _crawl_tasks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    task = _crawl_tasks[task_id]
    return TaskStatusResponse(
        status=task["status"],
        total=task["total"],
        completed=task["completed"],
        results=task["results"],
        errors=task["errors"],
    )


@router.delete("/task/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_crawl_task(task_id: str):
    """删除爬取任务"""
    if task_id not in _crawl_tasks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在"
        )

    del _crawl_tasks[task_id]
    return None


@router.post("/execute", response_model=TaskSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def execute_spider(request: SpiderTaskRequest, background_tasks: BackgroundTasks):
    """
    执行自定义爬虫代码
    
    爬虫代码示例:
    ```python
    class MySpider(Spider):
        name = "my_spider"
        
        async def parse(self, response):
            for item in response.css("div.item"):
                yield {
                    "title": item.css("h2::text").get(),
                }
    ```
    """
    task_id = str(uuid.uuid4())[:8]

    _crawl_tasks[task_id] = {
        "status": "pending",
        "total": 1,
        "completed": 0,
        "results": [],
        "errors": [],
    }

    async def run_spider():
        task = _crawl_tasks[task_id]
        task["status"] = "running"

        try:
            # 安全检查：禁止危险代码模式
            dangerous_patterns = [
                "import os", "from os", "__import__", "eval(", "exec(",
                "subprocess", "open(", "file(", "input(", "raw_input(",
                "compile(", "globals(", "locals(", "vars(", "dir(",
                "getattr(", "setattr(", "delattr(", "__builtins__",
                "importlib", "sys.modules", "os.system", "os.popen",
                "shutil", "pathlib", "pickle", "marshal", "shelve",
            ]
            code_lower = request.spider_code.lower()
            for pattern in dangerous_patterns:
                if pattern.lower() in code_lower:
                    raise ValueError(f"禁止使用危险代码模式: {pattern}")

            # 动态执行爬虫代码（受限命名空间）
            namespace = {
                "Spider": Spider,
                "Request": Request,
                "__builtins__": {},  # 禁用内置函数
            }
            exec(request.spider_code, namespace)

            # 查找 Spider 子类
            spider_class = None
            for name, obj in namespace.items():
                if isinstance(obj, type) and issubclass(obj, Spider) and obj is not Spider:
                    spider_class = obj
                    break

            if not spider_class:
                raise ValueError("未找到 Spider 子类")

            # 创建并运行爬虫
            spider = spider_class(**request.settings)
            if request.start_urls:
                spider.start_urls = request.start_urls

            result = await spider.run()
            task["results"].append(result.to_dict())
            task["completed"] = 1
            task["status"] = "completed"

        except Exception as e:
            logger.error(f"爬虫执行失败: {e}")
            task["errors"].append({"error": str(e)})
            task["status"] = "failed"

    background_tasks.add_task(run_spider)

    return TaskSubmitResponse(task_id=task_id, total=1)


@router.get("/client/stats")
async def get_client_stats():
    """获取 HTTP 客户端统计"""
    client = await get_client()
    return client.get_stats()
