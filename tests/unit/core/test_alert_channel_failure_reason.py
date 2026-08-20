"""告警渠道失败原因必须穿过 bool 边界抵达 API。

背景：钉钉返回的 ``token is not exist`` 过去只出现在一行日志里，渠道对外只回
bool，``/alert/test`` 因此恒显示 "dingtalk: 发送失败"。这些用例锁住"结构化码给
程序、第三方原文给人"两条线都不许再丢。
"""

import smtplib
from unittest.mock import AsyncMock

import httpx
import pytest
from antcode_core.application.services.alert.alert_channels import (
    DingtalkAlertChannel,
    EmailAlertChannel,
)
from antcode_core.application.services.alert.alert_channels.base import MultiWebhookChannel
from antcode_core.application.services.alert.alert_delivery_status import (
    ERROR_CHANNEL_BAD_RESPONSE,
    ERROR_CHANNEL_HTTP_STATUS,
    ERROR_CHANNEL_LEVEL_FILTERED,
    ERROR_CHANNEL_MIXED,
    ERROR_CHANNEL_NO_TARGET,
    ERROR_CHANNEL_REJECTED,
    ERROR_CHANNEL_SMTP_AUTH,
    REASON_UNAVAILABLE,
    channel_failed,
    merge_channel_outcomes,
)
from antcode_core.application.services.alert.alert_manager import alert_manager
from antcode_core.application.services.alert.alert_service import AlertService

DINGTALK_BAD_TOKEN = {"errcode": 300001, "errmsg": "token is not exist"}
WEBHOOK = {"name": "运维群", "url": "https://oapi.example.test/robot/send?access_token=bad"}


def _dingtalk_channel(response: httpx.Response, monkeypatch) -> DingtalkAlertChannel:
    """真实钉钉渠道 + 假传输层。被测的是原因传递，不是 HTTP 客户端。"""
    channel = DingtalkAlertChannel([dict(WEBHOOK)])
    channel.configure_retry(enabled=False, max_retries=1, retry_delay=0)
    monkeypatch.setattr(
        "antcode_core.application.services.alert.alert_channels.base.resolve_webhook_url",
        lambda url: object(),
    )
    monkeypatch.setattr(type(channel), "_post_payload", AsyncMock(return_value=response))
    return channel


def _service_with_channel(channel, monkeypatch) -> AlertService:
    service = AlertService()
    service._load_config_from_db = AsyncMock(return_value=AlertService._default_config())
    service._apply_config = AsyncMock()
    monkeypatch.setattr(alert_manager, "_channels", {channel.channel_name: channel})
    monkeypatch.setattr(alert_manager, "get_enabled_channels", lambda: [channel.channel_name])
    return service


@pytest.mark.asyncio
async def test_test_alert_surfaces_dingtalk_reason_instead_of_generic_failure(monkeypatch) -> None:
    """/alert/test 必须带出钉钉原文，而不是那句无信息的"发送失败"。"""
    channel = _dingtalk_channel(httpx.Response(200, json=DINGTALK_BAD_TOKEN), monkeypatch)
    service = _service_with_channel(channel, monkeypatch)

    result = await service.send_test_alert("dingtalk")

    assert result["success"] is False
    # 第三方原文（人看）——运维在 UI 上要能直接读到这句
    assert "token is not exist" in result["message"]
    assert "errcode=300001" in result["message"]
    # 结构化码（程序判定）
    assert ERROR_CHANNEL_REJECTED in result["result"]["errors"][0]
    assert "发送失败" not in result["result"]["errors"][0]


@pytest.mark.asyncio
async def test_channel_reports_http_status_with_body_as_detail(monkeypatch) -> None:
    channel = _dingtalk_channel(httpx.Response(502, text="upstream boom"), monkeypatch)

    outcome = await channel.send_alert_force("msg", "INFO")

    assert outcome.failure_code == ERROR_CHANNEL_HTTP_STATUS
    assert "502" in outcome.detail
    assert "upstream boom" in outcome.detail


@pytest.mark.asyncio
async def test_non_object_json_is_bad_response_not_provider_rejection(monkeypatch) -> None:
    """形状不对 != 第三方拒绝。两者过去都塌成 False，运维分不出是谁的问题。"""
    channel = _dingtalk_channel(httpx.Response(200, json=["not", "an", "object"]), monkeypatch)

    outcome = await channel.send_alert_force("msg", "INFO")

    assert outcome.failure_code == ERROR_CHANNEL_BAD_RESPONSE
    assert outcome.failure_code != ERROR_CHANNEL_REJECTED


class _SilentChannel(MultiWebhookChannel):
    """第三方判定失败但没给任何原文的渠道。"""

    def _build_payload(self, message, level):
        return {"message": message, "level": level}

    def _check_response(self, data):
        return False, ""

    @property
    def channel_name(self):
        return "silent"


@pytest.mark.asyncio
async def test_missing_provider_reason_is_declared_not_fabricated(monkeypatch) -> None:
    channel = _SilentChannel([dict(WEBHOOK)])
    channel.configure_retry(enabled=False, max_retries=1, retry_delay=0)
    monkeypatch.setattr(
        "antcode_core.application.services.alert.alert_channels.base.resolve_webhook_url",
        lambda url: object(),
    )
    monkeypatch.setattr(type(channel), "_post_payload", AsyncMock(return_value=httpx.Response(200, json={})))

    outcome = await channel.send_alert_force("msg", "INFO")

    assert outcome.failure_code == ERROR_CHANNEL_REJECTED
    # 多目标合并那一层拿不到原文时要明说（这里断的是 _merged_detail 的产出）
    assert REASON_UNAVAILABLE in outcome.detail


def test_describe_declares_missing_reason_instead_of_fabricating_one() -> None:
    """单独锁 describe() 自己的缺原因分支。

    上面那条走的是 _merged_detail，覆盖不到这里——实测把 describe() 的兜底
    换成"发送失败"时那条仍然全绿，属于假绿，故补这条。
    """
    described = channel_failed(ERROR_CHANNEL_REJECTED).describe()

    assert described == f"{ERROR_CHANNEL_REJECTED} ({REASON_UNAVAILABLE})"


@pytest.mark.asyncio
async def test_level_filtered_is_distinguishable_from_unconfigured() -> None:
    """两者过去都是裸 False，"没订阅这个级别"被误读成"没配渠道"。"""
    configured = DingtalkAlertChannel([{"name": "群", "url": "https://x.test/a", "levels": ["CRITICAL"]}])
    empty = DingtalkAlertChannel([])

    filtered = await configured.send_alert_for_level("msg", "INFO", ["INFO"])
    missing = await empty.send_alert_for_level("msg", "INFO", ["INFO"])

    assert filtered.failure_code == ERROR_CHANNEL_LEVEL_FILTERED
    assert missing.failure_code == ERROR_CHANNEL_NO_TARGET


@pytest.mark.asyncio
async def test_email_channel_surfaces_smtp_auth_reason(monkeypatch) -> None:
    """邮件是另一套同名实现（不继承 MultiWebhookChannel），不能被落下。"""
    channel = EmailAlertChannel(
        {
            "smtp_host": "smtp.example.test",
            "smtp_user": "alerts@example.test",
            "recipients": [{"email": "ops@example.test", "name": "Ops"}],
        }
    )
    channel.configure_retry(enabled=False, max_retries=1, retry_delay=0)

    def _reject(*_args, **_kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    monkeypatch.setattr(
        "antcode_core.application.services.alert.alert_channels.email.deliver_smtp_message",
        _reject,
    )

    outcome = await channel.send_alert_force("msg", "INFO")

    assert outcome.failure_code == ERROR_CHANNEL_SMTP_AUTH
    assert "Username and Password not accepted" in outcome.detail


def test_mixed_target_failures_keep_every_target_code() -> None:
    merged = merge_channel_outcomes(
        [
            ("群A", channel_failed(ERROR_CHANNEL_REJECTED, detail="token is not exist")),
            ("群B", channel_failed(ERROR_CHANNEL_HTTP_STATUS, detail="HTTP 502")),
        ]
    )

    assert merged.failure_code == ERROR_CHANNEL_MIXED
    assert ERROR_CHANNEL_REJECTED in merged.detail
    assert ERROR_CHANNEL_HTTP_STATUS in merged.detail
    assert "token is not exist" in merged.detail


def test_uniform_target_failures_keep_the_precise_code() -> None:
    merged = merge_channel_outcomes(
        [
            ("群A", channel_failed(ERROR_CHANNEL_REJECTED, detail="token is not exist")),
            ("群B", channel_failed(ERROR_CHANNEL_REJECTED, detail="keywords not in content")),
        ]
    )

    assert merged.failure_code == ERROR_CHANNEL_REJECTED
    assert "keywords not in content" in merged.detail


def test_failure_without_code_is_rejected_at_construction() -> None:
    """无码的失败等于退回"发送失败"，构造期就该炸。"""
    with pytest.raises(ValueError):
        channel_failed("")
