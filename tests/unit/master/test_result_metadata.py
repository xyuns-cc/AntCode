from antcode_master.control.result_metadata import merge_result_data


def test_result_merge_preserves_retry_source_metadata():
    current = {
        "retry_source_run_id": "run-failed",
        "retry_intent": {"retry_count": 1},
    }

    merged = merge_result_data(current, {"distributed": True, "pending": True})

    assert merged == {
        "retry_source_run_id": "run-failed",
        "retry_intent": {"retry_count": 1},
        "distributed": True,
        "pending": True,
    }
    assert current == {
        "retry_source_run_id": "run-failed",
        "retry_intent": {"retry_count": 1},
    }


def test_result_merge_updates_runtime_fields_without_dropping_metadata():
    merged = merge_result_data(
        {"retry_source_run_id": "run-failed", "status": "running"},
        {"status": "failed", "error": "boom"},
    )

    assert merged["retry_source_run_id"] == "run-failed"
    assert merged["status"] == "failed"
    assert merged["error"] == "boom"
