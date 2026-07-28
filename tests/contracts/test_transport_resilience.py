"""
Resilience contract — slow paths that previously caused subtle prod bugs.

These are explicitly *behavioral* tests, not chaos tests.  The goal is:
"under realistic adverse conditions, does the contract still hold?"
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


async def test_poll_task_timeout_returns_quickly(transport):
    """`poll_task(timeout=T)` must return within roughly T seconds — not
    block forever and not return immediately on an empty queue."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    msg = await transport.poll_task(timeout=1.0)
    elapsed = loop.time() - t0
    assert msg is None
    # Generous upper bound: must respect the timeout but not blow past it.
    assert elapsed < 5.0, f"poll_task(timeout=1) took {elapsed:.2f}s"


async def test_poll_control_timeout_returns_quickly(transport):
    """Same bound for the control channel."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    msg = await transport.poll_control(timeout=1.0)
    elapsed = loop.time() - t0
    assert msg is None
    assert elapsed < 5.0, f"poll_control(timeout=1) took {elapsed:.2f}s"


async def test_stop_with_grace_period_completes(transport):
    """A graceful stop must complete within `grace_period + epsilon`."""
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await transport.stop(grace_period=1.0)
    elapsed = loop.time() - t0
    # Allow generous slack — implementations may need a moment to drain.
    assert elapsed < 5.0, f"stop(grace=1) took {elapsed:.2f}s"
    assert transport.is_running is False


async def test_unacked_task_is_reclaimable_after_disconnect(
    transport,
    task_producer,
    *,
    fresh_ids,
    contract_probe,
):
    """An unacked delivery becomes visible after the production timeout."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
            "project_type": "code",
            "source_bundle_uri": f"pgartifact://{_EMPTY_SHA256}",
            "source_bundle_sha256": _EMPTY_SHA256,
            "source_bundle_size": "0",
            "transfer_method": "source_bundle",
        },
    )

    first = await transport.poll_task(timeout=2.0)
    assert first is not None
    assert first.receipt

    await transport.stop(grace_period=0.1)
    await contract_probe.advance_unacked_visibility(first.receipt)

    assert await transport.start() is True
    await contract_probe.trigger_unacked_reclaim()

    redelivered = await transport.poll_task(timeout=2.0)
    assert redelivered is not None
    assert redelivered.task_id == fresh_ids.task_id
    assert redelivered.run_id == fresh_ids.run_id
    assert redelivered.receipt

    assert await transport.ack_task(redelivered.receipt, accepted=True) is True


async def test_state_change_callback_fires(transport, fresh_ids):  # noqa: ARG001
    """Registering an `on_state_change` callback must fire when state
    transitions (here: stop → OFFLINE)."""
    events: list[tuple[str, str]] = []

    def cb(old, new):
        events.append((old.value, new.value))

    transport.on_state_change(cb)
    await transport.stop(grace_period=0.1)

    # We required at least one transition into OFFLINE.
    assert any(new == "offline" for _old, new in events), f"expected a transition into OFFLINE, got {events!r}"
