# antcode-scrapy

AntCode 规则爬虫的 Scrapy 执行引擎。

## 定位

worker 侧 `RulePlugin` 派发到子进程后，由本包接管**执行**：

- 输入：`--rule-file` 指向的 JSON（`ProjectRule.to_dispatch_dict()` 输出）
- 输出：items 先写入本地 `0600` spool，由 Worker 父进程通过可信 transport
  写入 Redis Stream `{{ns}}:spider:<run_id>:data`，字段格式与
  `SpiderDataItem.to_redis_dict()` 严格一致（跨包契约不可动）
- 元数据：`{{ns}}:spider:<run_id>:meta` hash
- 项目索引：`{{ns}}:spider:index:<project_id>`（活动时间）+
  `{{ns}}:spider:index:expiry:<project_id>`（有限 TTL 的绝对过期时间）ZSET

## 命令行

```bash
python -m antcode_scrapy.crawl --rule-file /path/to/rule.json
```

环境变量（由 `RulePlugin.build_plan` 注入）：

- `ANTCODE_SPIDER_RUN_ID`
- `ANTCODE_SPIDER_PROJECT_ID`
- `ANTCODE_SPIDER_SINK_MODE=spool`
- `ANTCODE_SPIDER_SPOOL_PATH`

子进程不会获得 Worker Redis 凭据；`redis` sink mode 已显式停用。

## 引擎切换

`rule.engine` 决定 Scrapy DownloadHandler：

- `requests`（默认）— Scrapy 内置
- `curl_cffi` — `scrapy-impersonate`（可选，见 settings）
- `playwright` / `render` — `scrapy-playwright`
