from pathlib import Path

import pytest
from antcode_worker.config import DATA_ROOT, WorkerConfig, _normalize_path
from antcode_worker.transport.base import ServerConfig
from antcode_worker.transport.redis import RedisTransport


def test_worker_config_has_no_local_redis_default():
    config = WorkerConfig()

    assert config.redis_url == ""


def test_transport_config_has_no_local_redis_default():
    config = ServerConfig()

    assert config.redis_url == ""


def test_redis_transport_requires_explicit_redis_url():
    try:
        RedisTransport()
    except ValueError as exc:
        assert "redis_url" in str(exc)
    else:
        raise AssertionError("RedisTransport must reject missing redis_url")


def test_worker_directories_do_not_create_task_log_storage(tmp_path):
    config = WorkerConfig(data_dir=str(tmp_path))

    config.ensure_directories()

    assert Path(config.venvs_dir).is_dir()
    assert Path(config.runs_dir).is_dir()
    assert not (tmp_path / "logs").exists()


def test_worker_default_data_root_is_project_data_directory():
    assert DATA_ROOT == Path.cwd() / "data" / "worker"


def test_worker_path_config_rejects_non_data_directory(tmp_path):
    with pytest.raises(ValueError, match="data"):
        _normalize_path(str(tmp_path))
