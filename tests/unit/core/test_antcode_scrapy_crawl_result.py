import pytest
from antcode_scrapy.crawl import CrawlOutcome, _result_exit_code


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (CrawlOutcome(0, 0, 0, 0, 0, 0), 1),
        (CrawlOutcome(1, 0, 1, 0, 0, 0), 0),
        (CrawlOutcome(2, 0, 1, 0, 0, 0), 1),
        (CrawlOutcome(1, 0, 1, 1, 0, 0), 1),
        (CrawlOutcome(1, 0, 1, 0, 1, 0), 1),
        (CrawlOutcome(1, 0, 1, 0, 0, 1), 1),
    ],
)
def test_rule_crawl_result_requires_nonempty_fully_persisted_items(outcome, expected):
    assert _result_exit_code(outcome) == expected
