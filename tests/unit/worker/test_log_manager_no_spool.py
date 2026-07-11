"""Worker LogManager 不使用本地 spool 恢复路径。"""

import pytest
from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.logs.manager import LogManager


class _Transport:
    is_connected = True

    async def send_log(self, log):
        return True

    async def send_log_batch(self, logs):
        return True


@pytest.mark.asyncio
async def test_log_manager_does_not_create_spool():
    manager = LogManager(run_id="run-1", transport=_Transport())

    await manager.start()
    await manager.write(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line"))
    await manager.stop()

    assert not hasattr(manager, "_spool")
    assert not hasattr(manager, "recover_from_spool")
    assert not hasattr(manager, "ack_logs")
