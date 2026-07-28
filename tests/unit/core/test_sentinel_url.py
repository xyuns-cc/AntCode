from __future__ import annotations

from typing import Any

import pytest
from antcode_core.infrastructure.redis import sentinel_client
from antcode_core.infrastructure.redis.factory import (
    create_async_redis_client,
    create_sync_redis_client,
)
from antcode_core.infrastructure.redis.sentinel_url import parse_sentinel_url

LEGACY_DATABASE = 2
SECOND_SENTINEL_PORT = 26380
MASTER_DATABASE = 3
MAX_CONNECTIONS = 17


def test_sentinel_url_parses_legacy_master_password_endpoints_and_db() -> None:
    config = parse_sentinel_url("redis+sentinel://secret@primary@sentinel-a:26379,sentinel-b/2")

    assert config.master_name == "primary"
    assert config.endpoints == (("sentinel-a", 26379), ("sentinel-b", 26379))
    assert config.database == LEGACY_DATABASE
    assert config.master_password == "secret"
    assert config.sentinel_password is None
    assert config.tls is False


def test_sentinel_url_parses_independent_percent_encoded_credentials() -> None:
    config = parse_sentinel_url(
        "rediss+sentinel://primary@[2001:db8::1]:26379,sentinel-b/0"
        "?sentinel_username=sentinel-user&sentinel_password=sentinel%40pass"
        "&master_username=app-user&master_password=master%26pass"
    )

    assert config.endpoints == (("2001:db8::1", 26379), ("sentinel-b", 26379))
    assert config.sentinel_username == "sentinel-user"
    assert config.sentinel_password == "sentinel@pass"
    assert config.master_username == "app-user"
    assert config.master_password == "master&pass"
    assert config.tls is True
    assert "pass" not in repr(config)


def test_standard_url_uses_configured_master_name_for_mode_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_SENTINEL_MASTER_NAME", "configured-master")

    config = parse_sentinel_url("redis://sentinel-a:26379,sentinel-b:26379/0")

    assert config.master_name == "configured-master"
    assert config.endpoints == (("sentinel-a", 26379), ("sentinel-b", 26379))


@pytest.mark.parametrize(
    "url",
    [
        "redis+sentinel://primary@sentinel-a:not-a-port/0",
        "redis+sentinel://primary@sentinel-a:70000/0",
        "redis+sentinel://primary@sentinel-a:26379/not-a-db",
        "redis+sentinel://primary@/0",
        "redis+sentinel://primary@sentinel-a,,sentinel-b/0",
        "redis+sentinel://primary@sentinel-a,sentinel-a/0",
        "redis+sentinel://primary@2001:db8::1/0",
        "redis+sentinel://primary@bad_host/0",
        "redis+sentinel://primary@a.-bad.example/0",
        "redis+sentinel://primary@sentinel-a/0?unknown=value",
        "redis+sentinel://primary@sentinel-a/0?master_username=app",
        "redis+sentinel://primary@sentinel-a/0?sentinel_username=sentinel",
        "redis+sentinel://primary@sentinel-a/0?sentinel_password=",
        "redis+sentinel://primary@sentinel-a/0?ssl_ca_certs=%2Fca.pem",
        "rediss+sentinel://primary@sentinel-a/0?ssl_certfile=%2Fclient.pem",
        "redis+sentinel://primary@sentinel-a/0?master_password=one&master_password=two",
        "redis+sentinel://legacy@primary@sentinel-a/0?master_password=new",
        "http://primary@sentinel-a/0",
    ],
)
def test_invalid_sentinel_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        parse_sentinel_url(url)


def test_validation_error_does_not_disclose_credential_value() -> None:
    secret = "do-not-log-this-value"

    with pytest.raises(ValueError) as exc_info:
        parse_sentinel_url(f"redis+sentinel://primary@sentinel-a/0?master_password={secret}&master_password=duplicate")

    assert secret not in str(exc_info.value)


class _SentinelRecorder:
    instances: list[_SentinelRecorder] = []

    def __init__(
        self,
        endpoints: list[tuple[str, int]],
        *,
        sentinel_kwargs: dict[str, Any],
        **master_kwargs: Any,
    ) -> None:
        self.endpoints = endpoints
        self.sentinel_kwargs = sentinel_kwargs
        self.master_kwargs = master_kwargs
        self.master_call: tuple[str, type[Any]] | None = None
        self.instances.append(self)

    def master_for(self, service_name: str, *, redis_class: type[Any]) -> object:
        self.master_call = (service_name, redis_class)
        return self


@pytest.mark.parametrize("is_async", [True, False])
def test_factory_applies_tls_and_separate_credentials_to_both_planes(
    monkeypatch: pytest.MonkeyPatch,
    is_async: bool,
) -> None:
    _SentinelRecorder.instances.clear()
    class_name = "AsyncSentinel" if is_async else "SyncSentinel"
    monkeypatch.setattr(sentinel_client, class_name, _SentinelRecorder)
    url = (
        "rediss+sentinel://primary@sentinel-a:26379,sentinel-b:26380/3"
        "?sentinel_username=sentinel-user&sentinel_password=sentinel-secret"
        "&master_username=master-user&master_password=master-secret"
        "&ssl_ca_certs=%2Fca.pem"
    )

    create = create_async_redis_client if is_async else create_sync_redis_client
    result = create(url, decode_responses=True, max_connections=MAX_CONNECTIONS)

    recorder = _SentinelRecorder.instances[0]
    assert result is recorder
    assert recorder.endpoints == [("sentinel-a", 26379), ("sentinel-b", SECOND_SENTINEL_PORT)]
    assert recorder.sentinel_kwargs["ssl"] is True
    assert recorder.sentinel_kwargs["ssl_ca_certs"] == "/ca.pem"
    assert recorder.sentinel_kwargs["username"] == "sentinel-user"
    assert recorder.sentinel_kwargs["password"] == "sentinel-secret"
    assert "db" not in recorder.sentinel_kwargs
    assert recorder.master_kwargs["ssl"] is True
    assert recorder.master_kwargs["ssl_ca_certs"] == "/ca.pem"
    assert recorder.master_kwargs["username"] == "master-user"
    assert recorder.master_kwargs["password"] == "master-secret"
    assert recorder.master_kwargs["db"] == MASTER_DATABASE
    assert recorder.master_kwargs["max_connections"] == MAX_CONNECTIONS
    assert recorder.master_call is not None
    assert recorder.master_call[0] == "primary"


@pytest.mark.parametrize("is_async", [True, False])
def test_factory_rejects_tls_options_on_plaintext_sentinel(
    is_async: bool,
) -> None:
    create = create_async_redis_client if is_async else create_sync_redis_client

    with pytest.raises(ValueError, match="rediss\\+sentinel"):
        create("redis+sentinel://primary@sentinel-a/0", ssl_ca_certs="/ca.pem")
