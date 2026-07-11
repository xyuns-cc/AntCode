import pytest
from antcode_core.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


@pytest.mark.asyncio
async def test_half_open_permit_is_released_between_successful_probes():
    breaker = CircuitBreaker(
        "probe",
        CircuitBreakerConfig(
            half_open_max_calls=1,
            success_threshold=2,
        ),
    )
    breaker._state = CircuitState.HALF_OPEN

    assert await breaker.call(lambda: "first") == "first"
    assert breaker._state == CircuitState.HALF_OPEN
    assert breaker._half_open_calls == 0

    assert await breaker.call(lambda: "second") == "second"
    assert breaker._state == CircuitState.CLOSED
