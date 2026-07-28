import pytest
from antcode_web_api.routes.v1.runtime_models import CreateEnvRequest
from pydantic import ValidationError


@pytest.mark.parametrize("version", ["3", "python3.11", "3.11@evil", "../3.11", "3.11 "])
def test_create_env_request_rejects_invalid_python_version(version: str) -> None:
    with pytest.raises(ValidationError):
        CreateEnvRequest(scope="shared", python_version=version)


@pytest.mark.parametrize("version", ["3.11", "3.11.11", "3.12"])
def test_create_env_request_accepts_numeric_python_version(version: str) -> None:
    request = CreateEnvRequest(scope="shared", python_version=version)

    assert request.python_version == version
