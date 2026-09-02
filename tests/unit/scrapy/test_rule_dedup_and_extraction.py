"""规则爬虫去重键与抽取规则的失败可见性回归。

每条用例都成对出现：一条钉住"必须响亮失败"，一条钉住"合法输入仍必须照常
工作"，避免把 pipeline 改成无脑抛异常也能全绿。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_scrapy.pipelines.dedup_pipeline import AntCodeDedupPipeline
from antcode_scrapy.spiders.rule_spider import UniversalRuleSpider

_DEDUP_URL_ENVS = (
    "ANTCODE_SPIDER_DEDUP_REDIS_URL",
    "ANTCODE_SPIDER_REDIS_URL",
    "REDIS_URL",
)


def _spider(**rule_extra) -> UniversalRuleSpider:
    rule = {
        "target_url": "https://example.com/list",
        "extraction_rules": [{"desc": "标题", "type": "css", "expr": "h1::text"}],
        **rule_extra,
    }
    return UniversalRuleSpider(rule=rule, run_id="run-1", project_id="project-1")


class _Response:
    """只实现 spider 真正用到的 Selector 接口。"""

    url = "https://example.com/list"
    text = "<html><body><h1>标题A</h1></body></html>"

    def __init__(self, values: list[str] | None = None, error: Exception | None = None) -> None:
        self._values = values or []
        self._error = error

    def css(self, expr: str):
        del expr
        if self._error is not None:
            raise self._error
        return SimpleNamespace(getall=lambda: list(self._values))

    def xpath(self, expr: str):
        return self.css(expr)


# ---------------------------------------------------------------------------
# dedup 摘要：fields 与 item 键对不上时必须报错，而不是产出恒定摘要
# ---------------------------------------------------------------------------


def test_digest_raises_when_no_configured_field_exists_on_item() -> None:
    """fields 全部缺失时，老实现给每条 item 相同摘要 → 第一条之后全被 DropItem。"""
    pipeline = AntCodeDedupPipeline()
    pipeline._fields = ["title", "url"]

    with pytest.raises(ValueError, match="一个都不存在"):
        pipeline._compute_digest({"_url": "https://example.com/1", "标题": "新闻A"})


def test_digest_distinguishes_items_when_configured_field_exists() -> None:
    """正向控制组：字段真的存在时，不同内容必须得到不同摘要。"""
    pipeline = AntCodeDedupPipeline()
    pipeline._fields = ["标题", "缺失字段"]

    first = pipeline._compute_digest({"标题": "新闻A"})
    second = pipeline._compute_digest({"标题": "新闻B"})

    assert first != second
    # 部分字段缺失仍然合法（占位空串），只有"全缺"才是配置错误
    assert first == pipeline._compute_digest({"标题": "新闻A", "无关": 1})


# ---------------------------------------------------------------------------
# dedup 启用但跑不起来时必须中止，不能只留一行 warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_spider_raises_when_no_redis_url_available(monkeypatch) -> None:
    for name in _DEDUP_URL_ENVS:
        monkeypatch.delenv(name, raising=False)
    spider = SimpleNamespace(rule={"dedup_config": {"enabled": True, "fields": ["标题"]}})

    with pytest.raises(RuntimeError, match="Redis URL"):
        await AntCodeDedupPipeline().open_spider(spider)


@pytest.mark.asyncio
async def test_open_spider_raises_when_fields_missing(monkeypatch) -> None:
    monkeypatch.setenv("ANTCODE_SPIDER_DEDUP_REDIS_URL", "redis://127.0.0.1:6379/0")
    spider = SimpleNamespace(rule={"dedup_config": {"enabled": True, "fields": []}})

    with pytest.raises(RuntimeError, match="fields 为空"):
        await AntCodeDedupPipeline().open_spider(spider)


@pytest.mark.asyncio
async def test_open_spider_stays_silent_when_dedup_disabled(monkeypatch) -> None:
    """反向控制组：没开去重时不许报错，也不许把自己标成 enabled。"""
    for name in _DEDUP_URL_ENVS:
        monkeypatch.delenv(name, raising=False)
    pipeline = AntCodeDedupPipeline()

    await pipeline.open_spider(SimpleNamespace(rule={"dedup_config": {"enabled": False}}))
    await pipeline.open_spider(SimpleNamespace(rule={}))

    assert pipeline._enabled is False
    assert await pipeline.process_item({"标题": "新闻A"}, SimpleNamespace()) == {"标题": "新闻A"}


# ---------------------------------------------------------------------------
# 抽取规则：写错的规则必须炸，"选择器没匹配到"才返回 None
# ---------------------------------------------------------------------------


def test_apply_rule_raises_on_unsupported_type() -> None:
    """老实现里这条 raise 被自己的 except Exception 吃掉，恒返回 None。"""
    spider = _spider()

    with pytest.raises(ValueError, match="抽取规则执行失败"):
        spider._apply_rule(_Response(), {"desc": "标题", "type": "jsonpath", "expr": "$.a"})


def test_apply_rule_raises_on_broken_expression() -> None:
    spider = _spider()
    response = _Response(error=ValueError("invalid css selector"))

    with pytest.raises(ValueError, match="抽取规则执行失败"):
        spider._apply_rule(response, {"desc": "标题", "type": "css", "expr": "h1[["})


def test_apply_rule_raises_on_empty_expression() -> None:
    spider = _spider()

    with pytest.raises(ValueError, match="表达式为空"):
        spider._apply_rule(_Response(), {"desc": "标题", "type": "css", "expr": ""})


@pytest.mark.parametrize(
    "expr",
    ["(a+)+b", "x" * 513],
)
def test_apply_rule_raises_on_regex_rejected_by_safety_guard(expr: str) -> None:
    """被 ReDoS 兜底拒绝的正则原来静默返回 None，用户只看到空字段。"""
    spider = _spider()

    with pytest.raises(ValueError, match="抽取规则执行失败"):
        spider._apply_rule(_Response(), {"desc": "标题", "type": "regex", "expr": expr})


def test_apply_rule_returns_none_only_when_selector_matches_nothing() -> None:
    """正向控制组：合法表达式没匹配到内容仍是 None，不能一起被改成抛异常。"""
    spider = _spider()

    assert spider._apply_rule(_Response(values=[]), {"desc": "标题", "type": "css", "expr": "h1::text"}) is None
    assert spider._apply_rule(_Response(values=["A"]), {"desc": "标题", "type": "css", "expr": "h1::text"}) == "A"
    assert spider._apply_rule(_Response(values=["A", "B"]), {"desc": "标题", "type": "css", "expr": "h1::text"}) == [
        "A",
        "B",
    ]


def test_apply_rule_still_runs_valid_regex() -> None:
    spider = _spider()

    assert spider._apply_rule(_Response(), {"desc": "标题", "type": "regex", "expr": r"<h1>(.*?)</h1>"}) == "标题A"
