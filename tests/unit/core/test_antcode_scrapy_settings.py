"""Crawl batch limits must reach Scrapy's actual setting names."""

from antcode_scrapy.settings import build_settings


def test_build_settings_applies_concurrency_and_depth_limits():
    settings = build_settings(
        {
            "concurrent_requests": 17,
            "max_depth": 4,
            "timeout": 30,
        }
    )

    assert settings["CONCURRENT_REQUESTS"] == 17
    assert settings["DEPTH_LIMIT"] == 4
    assert settings["DOWNLOAD_TIMEOUT"] == 30
