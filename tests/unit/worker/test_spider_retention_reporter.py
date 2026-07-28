"""Legacy Direct Redis Spider reporter removal contract."""

from unittest.mock import MagicMock

import pytest
from antcode_worker.plugins.spider.data.reporter import RedisDataReporter
from antcode_worker.transport.redis.keys import RedisKeys


def test_direct_redis_reporter_is_explicitly_disabled() -> None:
    with pytest.raises(RuntimeError, match="Direct Redis Spider reporter 已停用"):
        RedisDataReporter(
            redis_client=MagicMock(),
            keys=RedisKeys(),
            run_id="run-1",
            project_id="project-1",
            spider_name="spider",
        )
