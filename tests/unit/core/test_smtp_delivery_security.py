import asyncio
import threading

import pytest
from antcode_core.application.services.alert import smtp_delivery
from antcode_core.application.services.alert.alert_channels.email import EmailAlertChannel
from antcode_core.application.services.alert.smtp_delivery import SMTPDeliveryConfig


def _delivery_config(host: str = "smtp.example.com") -> SMTPDeliveryConfig:
    return SMTPDeliveryConfig(
        host=host,
        port=465,
        username="alerts@example.com",
        password="test-only-password",
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "metadata.google.internal"])
def test_smtp_validation_rejects_private_and_metadata_targets(host: str) -> None:
    with pytest.raises(ValueError):
        smtp_delivery.validate_smtp_host(host)


def test_smtp_delivery_re_resolves_and_pins_connection_address(monkeypatch) -> None:
    resolutions = iter([("198.51.100.10",), ("198.51.100.20",)])
    connected: list[tuple[str, int, str]] = []

    def resolve(_host: str, *, allow_private: bool) -> tuple[str, ...]:
        assert allow_private is False
        return next(resolutions)

    class FakeSMTP:
        def connect(self, address: str, port: int) -> tuple[int, bytes]:
            connected.append((address, port, self._host))
            return 220, b"ready"

        def login(self, _username: str, _password: str) -> None:
            pass

        def sendmail(self, _sender: str, _recipients: list[str], _message: str) -> None:
            pass

        def quit(self) -> None:
            pass

        def close(self) -> None:
            pass

    fake_server = FakeSMTP()
    monkeypatch.setattr(smtp_delivery, "resolve_network_host_addresses", resolve)
    monkeypatch.setattr(smtp_delivery, "_new_smtp_server", lambda **_kwargs: fake_server)

    assert smtp_delivery.validate_smtp_host("SMTP.Example.com.") == "smtp.example.com"
    smtp_delivery.deliver_smtp_message(
        _delivery_config(),
        recipient_email="ops@example.com",
        message="alert",
    )

    assert connected == [("198.51.100.20", 465, "smtp.example.com")]


def test_smtp_delivery_rejects_rebound_private_address(monkeypatch) -> None:
    resolutions = iter([("198.51.100.10",), ValueError("目标主机解析到私网地址")])

    def resolve(_host: str, *, allow_private: bool) -> tuple[str, ...]:
        result = next(resolutions)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(smtp_delivery, "resolve_network_host_addresses", resolve)
    assert smtp_delivery.validate_smtp_host("smtp.example.com") == "smtp.example.com"

    with pytest.raises(ValueError, match="私网"):
        smtp_delivery.deliver_smtp_message(
            _delivery_config(),
            recipient_email="ops@example.com",
            message="alert",
        )


def test_smtp_ssl_connects_to_pinned_ip_but_verifies_original_host(monkeypatch) -> None:
    plain_socket = object()
    wrapped_socket = object()
    wrap_calls: list[tuple[object, str]] = []
    server = object.__new__(smtp_delivery._PinnedSMTPSSL)
    server._host = "smtp.example.com"
    server.source_address = None
    server.context = type(
        "Context",
        (),
        {
            "wrap_socket": lambda _self, sock, *, server_hostname: (
                wrap_calls.append((sock, server_hostname)) or wrapped_socket
            )
        },
    )()
    monkeypatch.setattr(smtp_delivery.socket, "create_connection", lambda *_args, **_kwargs: plain_socket)

    result = server._get_socket("198.51.100.20", 465, 10)

    assert result is wrapped_socket
    assert wrap_calls == [(plain_socket, "smtp.example.com")]


@pytest.mark.asyncio
async def test_email_delivery_does_not_block_event_loop(monkeypatch) -> None:
    delivery_thread_ids: list[int] = []
    delivery_started = threading.Event()
    release_delivery = threading.Event()

    def deliver(*_args, **_kwargs) -> None:
        delivery_thread_ids.append(threading.get_ident())
        delivery_started.set()
        assert release_delivery.wait(timeout=2)

    channel = EmailAlertChannel(
        {
            "smtp_host": "smtp.example.com",
            "smtp_user": "alerts@example.com",
            "smtp_password": "test-only-password",
        }
    )
    monkeypatch.setattr(
        "antcode_core.application.services.alert.alert_channels.email.deliver_smtp_message",
        deliver,
    )

    send_task = asyncio.create_task(
        channel._send_email(
            recipient_email="ops@example.com",
            recipient_name="Ops",
            subject="Alert",
            html_body="<p>Alert</p>",
        )
    )
    await asyncio.wait_for(asyncio.to_thread(delivery_started.wait), timeout=1)

    assert not send_task.done()
    assert delivery_thread_ids != [threading.get_ident()]
    release_delivery.set()
    assert await send_task is True
