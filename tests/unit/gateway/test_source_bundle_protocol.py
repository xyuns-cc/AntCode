"""Gateway source bundle 协议测试。"""

from antcode_contracts import data_pb2
from antcode_gateway.handlers.poll import TaskPollHandler


def test_task_dispatch_proto_removed_legacy_fields():
    field_names = {field.name for field in data_pb2.TaskDispatch.DESCRIPTOR.fields}

    assert "download_url" not in field_names
    assert "file_hash" not in field_names
    assert {
        "source_bundle_uri",
        "source_bundle_sha256",
        "source_bundle_size",
        "transfer_method",
        "entry_point",
        "resolved_revision",
        "source_subdir",
    }.issubset(field_names)


def test_parse_task_data_reads_only_source_bundle_fields():
    handler = TaskPollHandler(redis_client=None)

    task = handler._parse_task_data(
        data={
            "task_id": "task-1",
            "project_id": "proj-1",
            "download_url": "https://example.com/old.zip",
            "file_hash": "legacy",
            "source_bundle_uri": "pgartifact://" + "a" * 64,
            "source_bundle_sha256": "a" * 64,
            "source_bundle_size": "123",
            "transfer_method": "source_bundle",
            "entry_point": "main.py",
            "resolved_revision": "rev-1",
            "source_subdir": "spiders/news",
        },
        message_id="1-0",
    )

    assert task is not None
    assert not hasattr(task, "download_url")
    assert not hasattr(task, "file_hash")
    assert task.source_bundle_uri == "pgartifact://" + "a" * 64
    assert task.source_bundle_sha256 == "a" * 64
    assert task.source_bundle_size == 123
    assert task.transfer_method == "source_bundle"
    assert task.resolved_revision == "rev-1"
    assert task.source_subdir == "spiders/news"
