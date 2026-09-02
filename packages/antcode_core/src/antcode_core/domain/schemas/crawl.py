"""爬取批次相关的 Pydantic 模式定义"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _UniqueSeedURLs(BaseModel):
    @field_validator("seed_urls", check_fields=False)
    @classmethod
    def reject_duplicate_seed_urls(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("seed_urls 不允许重复")
        return value


# =============================================================================
# 请求模型
# =============================================================================


class CrawlBatchCreateRequest(_UniqueSeedURLs):
    """创建爬取批次请求"""

    project_id: str = Field(..., description="项目公开ID")
    name: str = Field(..., min_length=1, max_length=255, description="批次名称")
    description: str = Field("", max_length=1000, description="批次描述")
    # P1-29 集合大小兜底:1w 个种子 URL 已远超真实业务需要,超过就 422,
    # 避免 10M 条 URL 打爆 asyncpg / Pydantic 反序列化 OOM。
    seed_urls: list[str] = Field(..., min_length=1, max_length=10000, description="种子URL列表(最多 10000)")
    max_depth: int = Field(3, ge=1, le=10, description="最大爬取深度")
    max_pages: int = Field(10000, ge=1, le=1000000, description="最大爬取页面数")
    max_concurrency: int = Field(50, ge=1, le=500, description="最大并发数")
    request_delay: float = Field(0.5, ge=0, le=60, description="请求间隔(秒)")
    timeout: int = Field(30, ge=1, le=300, description="请求超时(秒)")
    max_retries: int = Field(3, ge=0, le=10, description="最大重试次数")


class CrawlBatchTestRequest(_UniqueSeedURLs):
    """测试执行请求"""

    project_id: str = Field(..., description="项目公开ID")
    seed_urls: list[str] = Field(..., min_length=1, max_length=10, description="种子URL列表")
    max_depth: int = Field(2, ge=1, le=3, description="最大爬取深度")
    max_pages: int = Field(10, ge=1, le=100, description="最大爬取页面数")


# =============================================================================
# 响应模型
# =============================================================================


class CrawlBatchResponse(BaseModel):
    """爬取批次响应"""

    id: str = Field(..., description="批次公开ID")
    project_id: str = Field(..., description="项目公开ID")
    name: str = Field(..., description="批次名称")
    description: str = Field("", description="批次描述")
    seed_urls: list[str] = Field(..., description="种子URL列表")
    max_depth: int = Field(..., description="最大爬取深度")
    max_pages: int = Field(..., description="最大爬取页面数")
    max_concurrency: int = Field(..., description="最大并发数")
    request_delay: float = Field(..., description="请求间隔(秒)")
    timeout: int = Field(..., description="请求超时(秒)")
    max_retries: int = Field(..., description="最大重试次数")
    status: str = Field(..., description="批次状态")
    is_test: bool = Field(..., description="是否为测试批次")
    created_at: datetime = Field(..., description="创建时间")
    # S7 stress fix: model 里这俩字段是 nullable datetime，Pydantic 会
    # 拿到 datetime 或 None。之前定义 str 让 running 的 batch 详情端点直接
    # 500，也没被 list 端点覆盖（新建的 batch 全 None 反而通过）。改成
    # datetime | None 让 Pydantic 自动序列化 ISO 字符串。
    started_at: datetime | None = Field(None, description="开始时间")
    completed_at: datetime | None = Field(None, description="完成时间")

    model_config = ConfigDict(from_attributes=True)


class BatchProgressResponse(BaseModel):
    """批次进度响应"""

    batch_id: str = Field(..., description="批次公开ID")
    total_urls: int = Field(0, description="总URL数")
    pending_urls: int = Field(0, description="待处理数")
    completed_urls: int = Field(0, description="已完成数")
    failed_urls: int = Field(0, description="失败数")
    active_workers: int = Field(0, description="活跃Worker数")
    speed_per_minute: float = Field(0.0, description="速度(URLs/分钟)")
    last_updated: str | None = Field(default=None, description="最后更新时间")


class CrawlTestResultResponse(BaseModel):
    """测试执行结果响应"""

    batch_id: str = Field(..., description="测试批次ID")
    success: bool = Field(..., description="是否成功")
    total_pages: int = Field(0, description="爬取页面数")
    success_pages: int = Field(0, description="成功页面数")
    failed_pages: int = Field(0, description="失败页面数")
    sample_data: list[dict] = Field(default_factory=list, description="样本数据")
    errors: list[str] = Field(default_factory=list, description="错误信息列表")
    duration_seconds: float = Field(0.0, description="执行耗时(秒)")


class TestStatusResponse(BaseModel):
    """测试状态响应"""

    batch_id: str = Field("", description="批次ID")
    status: str = Field(..., description="状态(pending/running/completed/failed/not_found)")
    progress: BatchProgressResponse | None = Field(default=None, description="进度信息")
    started_at: str | None = Field(default=None, description="开始时间")
    completed_at: str | None = Field(default=None, description="完成时间")
