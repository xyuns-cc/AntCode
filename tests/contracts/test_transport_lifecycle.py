"""
Lifecycle contract — `start()`, `stop()`, `state`, `is_running`, `mode`.

Both implementations must behave identically here.  Anything that changes
this contract should be controversial enough to require a separate design
doc; do not relax these tests lightly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_mode_property_matches_factory_choice(transport, transport_mode):
    """`transport.mode` must reflect the mode the caller asked for."""
    from antcode_worker.transport.base import TransportMode

    expected = TransportMode.DIRECT if transport_mode == "redis" else TransportMode.GATEWAY
    assert transport.mode is expected


async def test_is_running_after_start(transport):
    """After `start()` returns True, `is_running` must be True."""
    assert transport.is_running is True


async def test_state_is_online_after_start(transport):
    """The post-start state must be ONLINE — anything else means start()
    only partially succeeded."""
    from antcode_worker.transport.base import WorkerState

    assert transport.state is WorkerState.ONLINE


async def test_start_is_idempotent(transport):
    """Calling start() again on a running transport must be a safe no-op
    that returns True (not raise, not flip state)."""
    second = await transport.start()
    assert second is True
    assert transport.is_running is True


async def test_stop_is_idempotent(transport):
    """Calling stop() twice in a row must not raise."""
    await transport.stop(grace_period=0.1)
    # second stop on an already-stopped transport must be a no-op
    await transport.stop(grace_period=0.1)
    assert transport.is_running is False


async def test_stop_clears_running_and_state(transport):
    """After stop(), `is_running` must be False and state must be OFFLINE."""
    from antcode_worker.transport.base import WorkerState

    await transport.stop(grace_period=0.5)
    assert transport.is_running is False
    assert transport.state is WorkerState.OFFLINE


async def test_get_status_returns_dict(transport, transport_mode):
    """`get_status()` is the canonical introspection hook — it must always
    return a dict carrying at least `mode`, `state`, and `running`."""
    status = transport.get_status()
    assert isinstance(status, dict)
    assert "mode" in status
    assert "state" in status
    assert "running" in status
    # Mode in status should match the actual transport.mode value.
    assert status["mode"] == transport.mode.value
