from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_removed_log_storage_backend_files_do_not_exist():
    removed = [
        "packages/antcode_core/src/antcode_core/infrastructure/storage/log_storage/base.py",
        "packages/antcode_core/src/antcode_core/infrastructure/storage/log_storage/s3.py",
        "packages/antcode_core/src/antcode_core/infrastructure/storage/log_storage/clickhouse.py",
        "packages/antcode_core/src/antcode_core/infrastructure/storage/log_storage/local.py",
    ]

    for relative_path in removed:
        assert not (ROOT / relative_path).exists()


def test_gateway_log_handler_has_no_simple_ack_or_log_storage_imports():
    source = _read("services/gateway/src/antcode_gateway/handlers/logs.py")

    assert "get_log_storage" not in source
    assert "简单 ACK" not in source
    assert "S3" not in source
    assert "ClickHouse" not in source


def test_worker_log_manager_does_not_initialize_wal_or_s3_archive():
    source = _read("services/worker/src/antcode_worker/logs/manager.py")

    assert "LogArchiver" not in source
    assert "ArchiveConfig" not in source
    assert "wal_dir" not in source
    assert "S3" not in source


def test_worker_runtime_does_not_report_log_archive_fields():
    source = "\n".join(
        [
            _read("services/worker/src/antcode_worker/domain/models.py"),
            _read("services/worker/src/antcode_worker/engine/engine.py"),
            _read("services/worker/src/antcode_worker/transport/redis/codecs.py"),
        ]
    )

    assert "log_archived" not in source
    assert "log_archive_uri" not in source
    assert "archive_logs" not in source


def test_removed_worker_archive_modules_do_not_exist():
    removed = [
        "services/worker/src/antcode_worker/logs/wal.py",
        "services/worker/src/antcode_worker/logs/spool.py",
        "services/worker/src/antcode_worker/logs/archive.py",
        "services/worker/src/antcode_worker/logging/archiver.py",
    ]

    for relative_path in removed:
        assert not (ROOT / relative_path).exists()


def test_distributed_log_service_uses_postgres_not_filesystem():
    source = _read("packages/antcode_core/src/antcode_core/application/services/workers/distributed_log_service.py")

    assert "postgres_task_log_service" in source
    assert "open(" not in source
    assert "json_dump_file" not in source
    assert "json_load_file" not in source
    assert "_log_root" not in source
    assert "_write_to_file" not in source
