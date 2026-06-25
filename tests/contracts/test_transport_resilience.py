"""
Resilience contract — slow paths that previously caused subtle prod bugs.

These are explicitly *behavioral* tests, not chaos tests.  The goal is:
"under realistic adverse conditions, does the contract still hold?"
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


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


@pytest.mark.xfail(
    reason=(
        "Connection-drop / reclaim semantics aren't pinned down yet — the "
        "two transports differ (Direct uses XAUTOCLAIM, Gateway relies on "
        "Gateway-side bookkeeping). Revisit once P1 lands."
    ),
    strict=False,
)
async def test_unacked_task_is_reclaimable_after_disconnect(
    transport, task_producer, fresh_ids
):
    """Goal of this test (not yet enforceable): if a worker polls a task
    and then dies without acking, another worker on the same group must
    eventually be able to poll the same logical task again.

    Today this requires implementation-specific machinery (Direct's
    PendingTaskReclaimer vs. Gateway-side timeouts) and the timing
    knobs aren't part of the public contract, so we mark it xfail
    until P1 clarifies the semantics."""
    await task_producer(
        transport,
        {
            "task_id": fresh_ids.task_id,
            "project_id": fresh_ids.project_id,
            "run_id": fresh_ids.run_id,
        },
    )

    first = await transport.poll_task(timeout=2.0)
    assert first is not None

    # Simulate a hard disconnect by stopping mid-flight.
    await transport.stop(grace_period=0.1)

    # In a fully-contracted world a sibling consumer would now be able to
    # reclaim the unacked message. Today that's implementation-specific.
    raise AssertionError("contract not yet defined — see test docstring")


async def test_state_change_callback_fires(transport, fresh_ids):  # noqa: ARG001
    """Registering an `on_state_change` callback must fire when state
    transitions (here: stop → OFFLINE)."""
    events: list[tuple[str, str]] = []

    def cb(old, new):
        events.append((old.value, new.value))

    transport.on_state_change(cb)
    await transport.stop(grace_period=0.1)

    # We required at least one transition into OFFLINE.
    assert any(new == "offline" for _old, new in events), (
        f"expected a transition into OFFLINE, got {events!r}"
    )
