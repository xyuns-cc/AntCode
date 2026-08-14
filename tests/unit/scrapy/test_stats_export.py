import json
from types import SimpleNamespace

from antcode_scrapy.stats_export import STATS_PATH_ENV, SpiderStatsExporter

EXPECTED_REQUEST_COUNT = 3
EXPECTED_RESPONSE_COUNT = 2
EXPECTED_ERROR_COUNT = 2
EXPECTED_LATENCY_MS = 30.0


class _Stats:
    def __init__(self) -> None:
        self.values = {
            "downloader/request_count": 3,
            "downloader/response_count": 2,
            "downloader/response_status_count/200": 1,
            "downloader/response_status_count/500": 1,
            "downloader/exception_count": 1,
            "item_scraped_count": 1,
        }

    def get_value(self, key, default=0):
        return self.values.get(key, default)

    def get_stats(self):
        return dict(self.values)


class _Signals:
    def __init__(self) -> None:
        self.handlers = []

    def connect(self, handler, signal) -> None:
        self.handlers.append((handler, signal))


def test_exporter_writes_response_domain_and_error_stats(monkeypatch, tmp_path) -> None:
    output = tmp_path / "stats.json"
    monkeypatch.setenv(STATS_PATH_ENV, str(output))
    crawler = SimpleNamespace(stats=_Stats(), signals=_Signals())
    exporter = SpiderStatsExporter.from_crawler(crawler)
    exporter.response_received(
        SimpleNamespace(status=200, url="https://example.com/a"),
        SimpleNamespace(meta={"download_latency": 0.02}),
        SimpleNamespace(),
    )
    exporter.response_received(
        SimpleNamespace(status=500, url="https://example.com/b"),
        SimpleNamespace(meta={"download_latency": 0.04}),
        SimpleNamespace(),
    )

    exporter.spider_closed(SimpleNamespace(), "finished")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["request_count"] == EXPECTED_REQUEST_COUNT
    assert payload["response_count"] == EXPECTED_RESPONSE_COUNT
    assert payload["error_count"] == EXPECTED_ERROR_COUNT
    assert payload["avg_latency_ms"] == EXPECTED_LATENCY_MS
    assert payload["domain_stats"] == [
        {
            "domain": "example.com",
            "requests": 2,
            "successes": 1,
            "avg_latency_ms": 30.0,
        }
    ]
