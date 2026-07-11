import pytest
from antcode_core.infrastructure.redis.sentinel_url import parse_sentinel_url


def test_sentinel_url_parses_password_master_endpoints_and_db() -> None:
    master, endpoints, options = parse_sentinel_url("redis+sentinel://secret@primary@sentinel-a:26379,sentinel-b/2")

    assert master == "primary"
    assert endpoints == [("sentinel-a", 26379), ("sentinel-b", 26379)]
    assert options == {"password": "secret", "db": 2}


@pytest.mark.parametrize(
    "url",
    [
        "redis+sentinel://primary@sentinel-a:not-a-port/0",
        "redis+sentinel://primary@sentinel-a:70000/0",
        "redis+sentinel://primary@sentinel-a:26379/not-a-db",
        "redis+sentinel://primary@/0",
    ],
)
def test_invalid_sentinel_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        parse_sentinel_url(url)
