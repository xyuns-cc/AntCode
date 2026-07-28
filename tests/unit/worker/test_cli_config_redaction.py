import json

import yaml
from antcode_worker import cli
from antcode_worker.config import WorkerConfig

REDIS_PASSWORD = "userinfo-redis-password"
SENTINEL_PASSWORD = "query-sentinel-password"
QUERY_PASSWORD = "query-redis-password"
CLIENT_KEY = "/private/worker-client-key.pem"


def _sensitive_config() -> WorkerConfig:
    return WorkerConfig(
        name="diagnostic-worker",
        transport_mode="direct",
        redis_url=(
            f"rediss+sentinel://worker:{REDIS_PASSWORD}@redis-1:26379,redis-2:26379@primary/0"
            f"?sentinel_password={SENTINEL_PASSWORD}&password={QUERY_PASSWORD}&ssl=true"
        ),
        redis_namespace="visible-namespace",
        client_key=CLIENT_KEY,
        credential_store="persistent",
    )


def _capture_print_config(monkeypatch, config_format: str) -> str:
    messages: list[str] = []
    monkeypatch.setattr(cli.WorkerConfig, "load_from_file", lambda: _sensitive_config())
    monkeypatch.setattr(cli.logger, "info", lambda _template, message: messages.append(str(message)))

    cli.print_config(config_format)

    return messages[-1]


def test_print_config_redacts_credentials_in_json(monkeypatch) -> None:
    output = _capture_print_config(monkeypatch, "json")
    parsed = json.loads(output)

    assert REDIS_PASSWORD not in output
    assert SENTINEL_PASSWORD not in output
    assert QUERY_PASSWORD not in output
    assert CLIENT_KEY not in output
    assert parsed["redis_url"].startswith("rediss+sentinel://worker:***@redis-1:26379")
    assert "sentinel_password=***" in parsed["redis_url"]
    assert "password=***" in parsed["redis_url"]
    assert parsed["client_key"] == "***REDACTED***"
    assert parsed["name"] == "diagnostic-worker"
    assert parsed["redis_namespace"] == "visible-namespace"
    assert parsed["credential_store"] == "persistent"


def test_print_config_redacts_credentials_in_yaml(monkeypatch) -> None:
    output = _capture_print_config(monkeypatch, "yaml")
    parsed = yaml.safe_load(output)

    assert REDIS_PASSWORD not in output
    assert SENTINEL_PASSWORD not in output
    assert QUERY_PASSWORD not in output
    assert CLIENT_KEY not in output
    assert parsed["client_key"] == "***REDACTED***"
    assert parsed["credential_store"] == "persistent"
