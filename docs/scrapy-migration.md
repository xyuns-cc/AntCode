# 规则爬虫 Scrapy 迁移说明

## 结论

**规则爬虫执行引擎全面切换到 Scrapy**。原 `antcode_worker.plugins.spider.spiderkit`
（自研迷你爬虫框架）已删除，`RulePlugin` 现在生成 Scrapy 子进程执行计划。

## 变更点

| 位置 | 旧 | 新 |
|---|---|---|
| RulePlugin.build_plan 命令 | `-m antcode_worker.plugins.spider.run_rule` | `-m antcode_scrapy.crawl` |
| 执行引擎 | 自研 spiderkit（lxml + httpx） | Scrapy 2.16 |
| JS 渲染 | 未支持 | scrapy-playwright（`rule.engine=playwright`） |
| TLS/JA3 指纹 | curl_cffi 直接调 | scrapy-impersonate DownloadHandler（`engine=curl_cffi`） |
| URL 断点续爬 | 不支持 | scrapy-redis（`rule.resume_enabled=true`） |
| 代理池 | 不支持 | 平台级 Redis ZSET + `AntCodeProxyMiddleware`（`rule.proxy_config.enabled=true`） |

## 契约兼容点（**不变**）

- 数据 stream key：`{ns}:spider:data:{run_id}` — 字段与 `SpiderDataItem.to_redis_dict()`
  逐字对齐（`AntCodeRedisPipeline` 保证）
- 元数据 hash：`{ns}:spider:meta:{run_id}`
- 项目索引 ZSET：`{ns}:spider:index:{project_id}`
- 环境变量注入：`ANTCODE_SPIDER_RUN_ID / PROJECT_ID / REDIS_URL / REDIS_NAMESPACE`
- 规则表达式语法（css/xpath/regex）— parsel 与 spiderkit 的 Selector 语义一致

## 前端/规则数据零改动

前端已保存的 `extraction_rules`（含 css/xpath/regex）、`pagination_config`、
`headers/cookies/target_url` 直接兼容。新增字段 `resume_enabled`（BOOL，缺省
False）用于开启 scrapy-redis 断点续爬。

## 引擎选择

`ProjectRule.engine`：

- `requests`（默认）— Scrapy 内置 HTTP handler
- `curl_cffi` — scrapy-impersonate（TLS/JA3 指纹伪装）
- `playwright` — scrapy-playwright（浏览器渲染）

## 代理池架构

- Redis ZSET `{ns}:proxy:pool`，member=proxy_url，score=健康分
- Redis SET `{ns}:proxy:cooldown:{proxy}`，60s TTL 黑名单
- Scrapy `AntCodeProxyMiddleware` 从池挑代理挂到 `request.meta["proxy"]`，
  按响应升/降分并写冷却
- 探活 loop（未来接线到 master `proxy_health_loop`）负责补充与刷新

## Playwright 部署

`Dockerfile.worker` 需在切 USER 之前用 root 装系统依赖，之后用 appuser 装
chromium：

```dockerfile
# root 段
RUN apt-get update && apt-get install -y --no-install-recommends <playwright deps>
# appuser 段
RUN python -m playwright install chromium
```

裸机部署：`python -m playwright install chromium` 一次即可。

## 部署验证

E2E 五场景（本次交付时已验证通过）：

- CSS 抽取：example.com h1/p → 1 item
- XPath 抽取：`//h1/text()` `//a/@href` → link=`https://iana.org/domains/example`
- Regex 抽取：`Domain / example` 各命中 2 次
- 分页：quotes.toscrape.com 3 页 × 10 quotes = 30 items
- Playwright JS 渲染：quotes.toscrape.com/js/ → 10 quotes 从渲染后 DOM 抽出

## 兼容性说明

`SpiderPlugin`（TaskType.SPIDER，旧版通用爬虫任务）**保留**，但依赖已从
spiderkit 解除；短期内可继续跑历史任务，长期评估后再决定去留。
`plugins/spider/data/` 子包（`SpiderDataItem` / `SpiderMeta` / `RedisDataReporter`）
**保留**，因为契约模型与 web_api 消费端共用。

## S5: 内容级去重

Scrapy 内置的 ``RFPDupeFilter`` 只做单次 run 内 URL 指纹去重，scrapy-redis
版本能跨 run 共享但仍是 URL 层级。**业务去重**（如同 title / 同 detail_url
视为重复）必须在 pipeline 层做，通过 `dedup_config` 触发：

```json
{
  "enabled": true,
  "fields": ["title", "detail_url"],  // 参与哈希的字段（顺序敏感）
  "scope": "project",                  // project(跨 run) | run(单次)
  "ttl_days": 30,                      // 去重集 TTL；0=不过期
  "on_hit": "drop"                     // drop=丢弃 | log=仅记录
}
```

Redis key：
- scope=project → `{ns}:spider:dedup:{project_id}` (SET)
- scope=run     → `{ns}:spider:dedup:run:{run_id}`

命中即 `DropItem`，Redis stream 不写入重复项；Scrapy stats 会打
`antcode/dedup_checked` 和 `antcode/dedup_hit` 计数。

## S6: 翻页能力

`UniversalRuleSpider` 内置 4 种翻页方法，按 `pagination_config.method` 或
启发式推断激活。**同一个 run 内自展开多页**，不再依赖 scheduler 侧把
一个规则拆成 N 个 run（旧逻辑已 deprecated）。

| method | 触发方式 | 适用场景 |
|---|---|---|
| `click_element` | `next_page_rule`（CSS/XPath）抓 href | 有"下一页"链接的传统分页 |
| `url_pattern` | `url_template` 或 `target_url` 里带 `{page}`/`{}`/`{page_number}` | 页码在 URL 路径里 |
| `url_param` | `page_param="page"` + query 追加 `?page=N` | 页码在 query string 里 |
| `infinite_scroll` | Playwright 引擎 + `scroll_count/scroll_wait_ms` | JS 触发 AJAX 增量加载 |

`start_page`（缺省 1）+ `max_pages` 控制起止；每条 item 带 `_page_number`
方便下游追溯。Scrapy stats 打 `antcode/pages_crawled` 计数。

启发式推断顺序（`method` 未显式设置时）：`url_template/target_url` 含
占位符 → url_pattern；`page_param` 存在 → url_param；`next_page_rule`
存在 → click_element；否则 none（单页）。

## S7: `{N}` 起始页占位符 + JS 点击翻页

### 数字占位符 `{N}`

`target_url` / `url_template` 里的 `{N}` 是**数字占位符**：N 直接就是
起始页号，无需再配 `pagination_config.start_page`。前后无需路径分隔符，
任意位置都能命中。

| URL 模板 | 展开结果（max_pages=3） |
|---|---|
| `.../page/{1}/` | `.../page/1/`、`.../page/2/`、`.../page/3/` |
| `.../page/{5}/` | `.../page/5/`、`.../page/6/`、`.../page/7/` |
| `.../catalogue/page-{1}.html` | `.../page-1.html`、`.../page-2.html`、`.../page-3.html` |
| `.../list?p={0}` | `.../list?p=0`、`.../list?p=1`、`.../list?p=2` |

优先级：**URL 里的 `{N}` > `pagination_config.start_page` > 缺省 1**。

兼容旧占位符：`{}`（首个）、`{page}`、`{page_number}` 仍照旧支持，
起始页仍走 `start_page` 或缺省 1。

### JS 点击翻页 `js_click`

面向 next 元素不是 `<a href>` 而是 `<button>` 或 JS 拦截 `<a>` 的场景。
自动强制 Playwright 引擎。

```json
{
  "pagination_config": {
    "method": "js_click",
    "next_page_rule": "li.next a",
    "wait_after_click_ms": 1000,
    "max_pages": 5
  }
}
```

实现：**单次 HTTP 请求，多次 DOM 快照** —— Playwright 打开首页 → 抽取
item → `page.locator(selector).click()` → `wait_for_load_state('networkidle')`
+ 保底 `wait_after_click_ms` → `page.content()` 新 DOM 重建 response →
抽取下一页 item → 循环，直到 max_pages 或 selector 不再存在。

每条 item 仍带 `_url`（浏览器当前 URL） + `_page_number`（递增）；
Scrapy stats 里 `antcode/pages_crawled` 反映实际抓取页数。

## S8: next 元素定位方式（CSS / XPath / text）

`pagination_config.next_page_rule` 支持三种类型的定位表达式，对
`click_element` (HTTP) 和 `js_click` (Playwright) 都生效。

### 输入形式

**1. 简单字符串（老兼容 + 启发式识别）**

| 字符串前缀 | 判定类型 |
|---|---|
| `//` `..` `xpath=` | XPath |
| 其他 | CSS |

**2. 结构化对象（推荐，语法与 extraction_rules 一致）**

```json
{"type": "css",   "expr": "li.next a"}
{"type": "xpath", "expr": "//li[@class='next']/a"}
{"type": "xpath", "expr": "//li[@class='next']/a/@href"}
{"type": "text",  "expr": "next"}
```

### 各类型语义

| type | HTTP click_element | Playwright js_click |
|---|---|---|
| `css` | `response.css(expr)` 抓 href；自动补 `::attr(href)` | Playwright 原生 CSS locator |
| `xpath` | `response.xpath(expr)`；非 `@href` 结尾自动补 `/@href` | 前置 `xpath=` 前缀，避免 Playwright 歧义 |
| `text` | 拼 XPath `//a[contains(normalize-space(.), "{expr}")]/@href` 兜底 | Playwright `text=<expr>` 大小写不敏感匹配（推荐 js_click 用） |

### 何时选哪种

- **传统站点分页**：CSS 最简洁，如 `li.next a`
- **DOM 结构复杂/依赖属性**：XPath 更精确，如 `//nav//a[@rel='next']`
- **JS 分页 / 按钮无稳定 class**：`text=下一页` / `text=Next` 直接用可读文字定位
- **一次性抓 href 而非元素**：结构化 XPath `{"type":"xpath","expr":".../@href"}` 直接给 href

