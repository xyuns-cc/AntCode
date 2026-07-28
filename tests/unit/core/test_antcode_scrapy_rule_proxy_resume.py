"""Rule proxy/resume 在 spool 与 legacy 模式下的安全契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_core.domain.models.project import ProjectRule
from antcode_scrapy.proxy import AntCodeProxyMiddleware, resolve_fixed_proxy_url
from antcode_scrapy.settings import build_settings


def _clear_rule_env(monkeypatch) -> None:
    for name in (
        "ANTCODE_SPIDER_SINK_MODE",
        "ANTCODE_SPIDER_EGRESS_PROXY",
        "ANTCODE_SPIDER_REDIS_URL",
        "ANTCODE_SPIDER_REDIS_NAMESPACE",
        "ANTCODE_SPIDER_PROJECT_ID",
        "ANTCODE_SPIDER_RUN_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_spool_resume_rejects_even_when_host_has_redis_url(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)
    monkeypatch.setenv("ANTCODE_SPIDER_SINK_MODE", "spool")
    monkeypatch.setenv("ANTCODE_SPIDER_EGRESS_PROXY", "http://127.0.0.1:32001")
    monkeypatch.setenv("ANTCODE_SPIDER_REDIS_URL", "redis://worker-secret")

    with pytest.raises(RuntimeError, match="父进程 checkpoint"):
        build_settings({"resume_enabled": True})


def test_legacy_resume_missing_redis_is_explicit(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)

    with pytest.raises(RuntimeError, match="需要 ANTCODE_SPIDER_REDIS_URL"):
        build_settings({"resume_enabled": True})


def test_legacy_resume_uses_run_scoped_redis_keys(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)
    monkeypatch.setenv("ANTCODE_SPIDER_REDIS_URL", "redis://localhost/0")
    monkeypatch.setenv("ANTCODE_SPIDER_REDIS_NAMESPACE", "ns")
    monkeypatch.setenv("ANTCODE_SPIDER_PROJECT_ID", "project-1")
    monkeypatch.setenv("ANTCODE_SPIDER_RUN_ID", "run-1")

    settings = build_settings({"resume_enabled": True})

    assert settings["REDIS_URL"] == "redis://localhost/0"
    assert settings["SCHEDULER_QUEUE_KEY"] == "ns:scrapy:project-1:run-1:requests"
    assert settings["DUPEFILTER_KEY"] == "ns:scrapy:project-1:run-1:dupefilter"


def test_zero_retry_and_delay_are_preserved(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)

    settings = build_settings({"retry_count": 0, "request_delay": 0})
    dispatched = ProjectRule(
        target_url="https://example.com",
        retry_count=0,
        request_delay=0,
    ).to_dispatch_dict()

    assert settings["RETRY_TIMES"] == 0
    assert settings["DOWNLOAD_DELAY"] == 0
    assert dispatched["retry_count"] == 0
    assert dispatched["request_delay"] == 0


def test_legacy_fixed_proxy_applies_to_scrapy_and_playwright(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)
    rule = {
        "engine": "playwright",
        "proxy_config": {
            "enabled": True,
            "proxy_type": "http",
            "proxy_url": "proxy.example.com:8080",
            "username": "user",
            "password": "p@ss",
        },
    }

    settings = build_settings(rule)

    expected = "http://user:p%40ss@proxy.example.com:8080"
    assert settings["DOWNLOADER_MIDDLEWARES"] == {
        "antcode_scrapy.proxy.AntCodeProxyMiddleware": 749,
    }
    assert settings["PLAYWRIGHT_LAUNCH_OPTIONS"]["proxy"]["server"] == expected
    request = SimpleNamespace(meta={"proxy": "http://wrong-proxy"})
    AntCodeProxyMiddleware(expected).process_request(request, None)
    assert request.meta["proxy"] == expected


@pytest.mark.parametrize(
    "config, message",
    [
        ({"enabled": True}, "proxy_url"),
        ({"enabled": True, "proxy_url": "socks5://proxy:1080"}, "仅支持 http/https"),
        (
            {"enabled": True, "proxy_url": "http://proxy:8080", "rotation": True},
            "动态代理池",
        ),
        (
            {"enabled": True, "proxy_url": "http://proxy:8080", "proxy_list": ["http://p2:8080"]},
            "动态代理池",
        ),
    ],
)
def test_unsupported_proxy_configs_fail_explicitly(config: dict, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        resolve_fixed_proxy_url(config)


def test_spool_fixed_proxy_cannot_bypass_worker_safe_egress(monkeypatch) -> None:
    _clear_rule_env(monkeypatch)
    monkeypatch.setenv("ANTCODE_SPIDER_SINK_MODE", "spool")
    monkeypatch.setenv("ANTCODE_SPIDER_EGRESS_PROXY", "http://127.0.0.1:32001")

    with pytest.raises(RuntimeError, match="拒绝绕过安全代理"):
        build_settings(
            {
                "proxy_config": {
                    "enabled": True,
                    "proxy_type": "http",
                    "proxy_url": "http://proxy.example.com:8080",
                }
            }
        )
