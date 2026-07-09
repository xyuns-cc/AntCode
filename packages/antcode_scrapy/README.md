# antcode-scrapy

AntCode 规则爬虫的 Scrapy 执行引擎（替换 spiderkit）。

## 定位

worker 侧 `RulePlugin` 派发到子进程后，由本包接管**执行**：

- 输入：`--rule-file` 指向的 JSON（`ProjectRule.to_dispatch_dict()` 输出）
- 输出：items 通过 Redis Stream `{ns}:spider:data:{run_id}` 上报，字段格式与
  `SpiderDataItem.to_redis_dict()` 严格一致（跨包契约不可动）
- 元数据：`{ns}:spider:meta:{run_id}` hash + `{ns}:spider:index:{project_id}` ZSET

## 命令行

```bash
python -m antcode_scrapy.crawl --rule-file /path/to/rule.json
```

环境变量（由 `RulePlugin.build_plan` 注入）：

- `ANTCODE_SPIDER_RUN_ID`
- `ANTCODE_SPIDER_PROJECT_ID`
- `ANTCODE_SPIDER_REDIS_URL`（缺失则不写 Redis，只在 stdout 打印 item）
- `ANTCODE_SPIDER_REDIS_NAMESPACE`（缺省 `antcode`）

## 引擎切换

`rule.engine` 决定 Scrapy DownloadHandler：

- `requests`（默认）— Scrapy 内置
- `curl_cffi` — `scrapy-impersonate`（可选，见 settings）
- `playwright` / `render` — `scrapy-playwright`
