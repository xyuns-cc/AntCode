from antcode_core.application.services.crawl.spider_storage_cleanup import (
    SPIDER_CLEANUP_EVENT_RUN_LIMIT,
    iter_cleanup_run_batches,
)


def test_cleanup_event_batches_are_bounded_and_deduplicated() -> None:
    run_ids = [f"run-{index}" for index in range(401)] + ["run-0", " "]

    batches = iter_cleanup_run_batches(run_ids)

    assert [len(batch) for batch in batches] == [200, 200, 1]
    assert sum(len(batch) for batch in batches) == 401
    assert max(map(len, batches)) == SPIDER_CLEANUP_EVENT_RUN_LIMIT
