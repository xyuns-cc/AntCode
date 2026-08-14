"""Targeted test-alert delivery orchestration."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from antcode_core.application.services.alert.alert_manager import alert_manager

ChannelResult = tuple[str, bool, str | None]
ChannelSender = Callable[[str, str], Awaitable[ChannelResult]]


@dataclass(frozen=True)
class TestAlertDelivery:
    channel: str
    message: str
    timeout: float


async def deliver_test_alert(request: TestAlertDelivery, sender: ChannelSender) -> dict:
    enabled_channels = alert_manager.get_enabled_channels()
    if not enabled_channels:
        logger.warning("测试告警失败: 没有配置任何告警渠道")
        return _no_channels_result()
    selected_channels = _select_channels(request.channel, enabled_channels)
    if not selected_channels:
        logger.warning(f"测试告警失败: 渠道未启用 channel={request.channel}")
        return _disabled_channel_result(request.channel, enabled_channels)
    message = request.message.strip() or _default_message()
    return await _send_selected(
        selected_channels,
        message,
        timeout=request.timeout,
        sender=sender,
    )


async def _send_selected(
    channels: list[str],
    message: str,
    *,
    timeout: float,
    sender: ChannelSender,
) -> dict:
    try:
        tasks = [sender(channel, message) for channel in channels]
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
    except TimeoutError:
        logger.error(f"测试告警超时 ({timeout}s)")
        return {"success": False, "message": f"发送超时 ({timeout}s)，请检查网络连接", "result": {"error": "timeout"}}
    except Exception as exc:
        logger.error(f"测试告警异常: {exc}")
        return {"success": False, "message": f"发送异常: {exc}", "result": {"error": str(exc)}}
    return _result_from_channel_results(channels, results)


def summarize_test_results(results) -> tuple[int, int, list[str]]:
    successes = 0
    errors = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(str(result))
            continue
        channel_name, success, error = result
        if success:
            successes += 1
        else:
            errors.append(f"{channel_name}: {error}")
    return successes, len(results) - successes, errors


def _result_from_channel_results(channels: list[str], results) -> dict:
    success_count, fail_count, errors = summarize_test_results(results)
    if success_count:
        return {
            "success": True,
            "message": f"测试告警已发送: 成功 {success_count}, 失败 {fail_count}",
            "result": _result_details(
                channels,
                success_count,
                fail_count=fail_count,
                errors=errors or None,
            ),
        }
    logger.error(f"测试告警全部失败: {errors}")
    return {
        "success": False,
        "message": f"测试告警发送失败: {'; '.join(errors)}",
        "result": _result_details(channels, 0, fail_count=fail_count, errors=errors),
    }


def _result_details(
    channels: list[str],
    success_count: int,
    *,
    fail_count: int,
    errors,
) -> dict:
    return {
        "enabled_channels": channels,
        "success_count": success_count,
        "fail_count": fail_count,
        "errors": errors,
    }


def _select_channels(channel: str, enabled_channels: list[str]) -> list[str]:
    if channel == "all":
        return enabled_channels
    return [channel] if channel in enabled_channels else []


def _default_message() -> str:
    return f"这是一条测试告警消息 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"


def _no_channels_result() -> dict:
    return {
        "success": False,
        "message": "没有配置任何告警渠道，请先添加飞书/钉钉/企业微信 Webhook 或邮件配置",
        "result": {"enabled_channels": []},
    }


def _disabled_channel_result(channel: str, enabled_channels: list[str]) -> dict:
    return {
        "success": False,
        "message": f"告警渠道未启用: {channel}",
        "result": {"enabled_channels": enabled_channels},
    }


__all__ = ["TestAlertDelivery", "deliver_test_alert", "summarize_test_results"]
