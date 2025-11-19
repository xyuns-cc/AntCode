import json
from pathlib import Path
from urllib.parse import urlparse

import scrapy


class SpiderSpider(scrapy.Spider):
    name = "spider"
    allowed_domains = []
    start_urls = ["https://example.com"]

    def __init__(self, rule_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rule_file = rule_file
        self.task_payload = {}
        if rule_file:
            self.task_payload = self._load_payload(rule_file)

    def _load_payload(self, rule_file):
        try:
            path = Path(rule_file)
            if not path.exists():
                self.logger.error("规则文件不存在: %s", rule_file)
                return {}
            content = path.read_text(encoding="utf-8")
            payload = json.loads(content)
            self.logger.info("已读取规则文件: %s", rule_file)
            return payload
        except Exception as exc:  # noqa: BLE001
            self.logger.error("解析规则文件失败: %s", exc)
            return {}

    def start_requests(self):
        if not self.task_payload:
            self.logger.warning("未提供规则文件，使用默认起始URL。")
            yield from super().start_requests()
            return

        url = self.task_payload.get("url")
        if not url:
            self.logger.error("规则中缺少目标URL，终止请求。")
            return

        parsed = urlparse(url)
        if parsed.netloc:
            self.allowed_domains = [parsed.netloc]

        method = (self.task_payload.get("method") or "GET").upper()
        headers = self.task_payload.get("headers") or {}
        cookies = self.task_payload.get("cookies") or {}
        meta = dict(self.task_payload.get("meta") or {})
        dont_filter = self.task_payload.get("dont_filter") or False

        fetch_type = meta.get("fetch_type", "requests")
        meta["fetch_type"] = fetch_type
        if fetch_type == "browser":
            if method == "POST":
                self.logger.error(
                    "任务被丢弃：fetch_type=browser 不支持 POST 请求 -> %s",
                    url,
                )
                return
            meta["browser_request"] = True
        elif fetch_type == "curl_cffi":
            meta["curl_cffi_request"] = True
            options = meta.setdefault("curl_cffi_options", {})
            options.setdefault("impersonate", meta.get("impersonate", "chrome120"))
            options.setdefault("timeout", meta.get("timeout", 30))
            options.setdefault("verify", meta.get("verify", True))
            options.setdefault("allow_redirects", meta.get("allow_redirects", True))
            options.setdefault("http2", meta.get("http2", False))
            options.setdefault("as_json", options.get("as_json", True))

        raw_body = self.task_payload.get("data")
        if raw_body is not None and "request_body" not in meta:
            meta["request_body"] = raw_body

        body = raw_body
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")

        request = scrapy.Request(
            url=url,
            method=method,
            headers=headers,
            cookies=cookies,
            body=body if body else None,
            meta=meta,
            dont_filter=dont_filter,
            callback=self.parse,
        )
        yield request

    def parse(self, response, **kwargs):
        self.logger.info("本地爬虫获取响应: %s %s", response.status, response.url)
        yield {
            "url": response.url,
            "status": response.status,
            "meta": response.meta,
            "headers": dict(response.headers),
            "body_length": len(response.body),
        }
