from types import SimpleNamespace

import pytest
from antcode_web_api import response


def test_response_public_id_resolution_requires_public_id_attribute():
    obj = SimpleNamespace(user_id=123)

    with pytest.raises(ValueError, match="created_by_public_id"):
        response._resolve_public_id(obj, "created_by_public_id")


def test_response_public_id_resolution_does_not_accept_internal_id_fallback():
    source = response._resolve_public_id.__code__.co_names

    assert "fallback_attr" not in source
