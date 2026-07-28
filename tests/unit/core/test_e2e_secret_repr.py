"""E2E 失败诊断不得打印认证材料。"""

from tests.e2e.conftest import E2EConfig, SensitiveString

SECRET_MARKER = "failure-output-secret-marker"


def test_e2e_config_repr_omits_admin_password() -> None:
    config = E2EConfig(
        web_api_url="http://127.0.0.1:8000",
        admin_user="admin",
        admin_password=SECRET_MARKER,
        worker_id="worker-1",
        runtime_python_version="3.12",
        shared_env_name="shared-py312",
        poll_interval=1.0,
        poll_timeout=2.0,
        http_timeout=3.0,
        expected_transport_mode="gateway",
    )

    assert SECRET_MARKER not in repr(config)


def test_sensitive_string_repr_is_redacted_without_changing_value() -> None:
    value = SensitiveString(SECRET_MARKER)

    assert repr(value) == "<redacted>"
    assert str(value) == SECRET_MARKER
