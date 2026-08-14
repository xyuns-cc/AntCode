"""Engine identity preflight regressions."""

from unittest.mock import MagicMock

import pytest
from antcode_worker.engine.engine import Engine


@pytest.mark.asyncio
async def test_engine_start_rejects_missing_worker_identity() -> None:
    transport = MagicMock()
    transport.worker_id = None
    transport._worker_id = None
    transport._gateway_config = None
    transport._config = None
    engine = Engine(transport=transport, executor=MagicMock())

    with pytest.raises(RuntimeError, match="worker_id"):
        await engine.start()

    assert engine.get_stats()["running"] is False
    assert engine._poll_task is None
