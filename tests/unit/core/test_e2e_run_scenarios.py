import pytest

from tests.e2e.run_scenarios import RunScenario, render_scenario_source


def test_render_scenario_source_includes_delay_exit_and_log_token() -> None:
    scenario = RunScenario(
        expected_status="failed",
        log_delay_seconds=0.5,
        script_delay_seconds=1.5,
        exit_code=7,
    )

    source = render_scenario_source("E2E-FAILED-token", scenario)

    assert "E2E-FAILED-token" in source
    assert source.index("time.sleep(0.5)") < source.index("E2E-FAILED-token")
    assert "time.sleep(1.5)" in source
    assert "raise SystemExit(7)" in source


@pytest.mark.parametrize(
    "scenario",
    [
        RunScenario(expected_status="success"),
        RunScenario(expected_status="failed", retry_count=1),
        RunScenario(expected_status="timeout", timeout_seconds=1),
        RunScenario(expected_status="cancelled", script_delay_seconds=1),
    ],
)
def test_supported_scenarios_are_accepted(scenario: RunScenario) -> None:
    assert scenario.expected_status in {"success", "failed", "timeout", "cancelled"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_status": "unknown"},
        {"expected_status": "success", "log_delay_seconds": -1},
        {"expected_status": "success", "script_delay_seconds": -1},
        {"expected_status": "success", "timeout_seconds": 0},
        {"expected_status": "failed", "retry_count": -1},
        {"expected_status": "failed", "retry_delay": 0},
    ],
)
def test_invalid_scenario_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        RunScenario(**kwargs)
