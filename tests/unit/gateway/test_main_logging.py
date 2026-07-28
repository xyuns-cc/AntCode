import pytest
from antcode_gateway import main as gateway_main


class ExpectedInitStop(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_gateway_configures_safe_logging_before_database_init(monkeypatch):
    events: list[str] = []

    def configure_logging() -> None:
        events.append("logging")

    async def stop_at_database_init(*, service: str) -> None:
        assert service == "gateway"
        events.append("database")
        raise ExpectedInitStop

    monkeypatch.setattr(gateway_main, "setup_logging", configure_logging)
    monkeypatch.setattr(gateway_main, "init_db", stop_at_database_init)

    with pytest.raises(ExpectedInitStop):
        await gateway_main.main()

    assert events == ["logging", "database"]
