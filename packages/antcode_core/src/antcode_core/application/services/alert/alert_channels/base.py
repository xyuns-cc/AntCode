"""告警渠道基类"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

import httpx
from loguru import logger

from antcode_core.application.services.alert.alert_delivery_status import (
    ERROR_CHANNEL_BAD_RESPONSE,
    ERROR_CHANNEL_HTTP_STATUS,
    ERROR_CHANNEL_LEVEL_FILTERED,
    ERROR_CHANNEL_NETWORK,
    ERROR_CHANNEL_NO_TARGET,
    ERROR_CHANNEL_REJECTED,
    ERROR_CHANNEL_TIMEOUT,
    ERROR_CHANNEL_UNEXPECTED,
    ERROR_CHANNEL_URL_REJECTED,
    ChannelSendOutcome,
    channel_failed,
    channel_sent,
    merge_channel_outcomes,
)
from antcode_core.application.services.projects.git_url_security import resolve_webhook_url

# 第三方响应体只作为人读诊断带出，截断避免把整页 HTML 灌进日志与 API。
RESPONSE_DETAIL_LIMIT = 200
HTTP_OK = 200


def _safe_webhook_label(url):
    """日志用的 Webhook 标识：只取 host，避免把含 token 的 URL 写进日志。"""
    try:
        from urllib.parse import urlsplit

        return urlsplit(url).hostname or "webhook"
    except Exception:
        return "webhook"


def _webhook_name(webhook_config):
    return webhook_config.get("name") or _safe_webhook_label(webhook_config.get("url", ""))


class AlertChannel(ABC):
    """告警渠道抽象基类"""

    def __init__(self):
        self.retry_enabled = True
        self.max_retries = 3
        self.retry_delay = 1

    def configure_retry(self, enabled, max_retries, retry_delay):
        """配置重试参数"""
        self.retry_enabled = enabled
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @property
    @abstractmethod
    def channel_name(self):
        """渠道名称"""
        pass


class MultiWebhookChannel(AlertChannel):
    """支持多 Webhook 的告警渠道基类"""

    def __init__(self, webhooks):
        super().__init__()
        self.webhooks = webhooks if webhooks else []

    @abstractmethod
    def _build_payload(self, message, level):
        """构建发送载荷（子类实现）"""
        pass

    @abstractmethod
    def _check_response(self, data):
        """判定第三方响应是否成功，返回 ``(ok, 第三方原文)``（子类实现）。

        入参保证是 dict（形状由基类先校验），子类不必再自己兜异常。原文只用于
        人读诊断，判定成败一律看第一个返回值。
        """
        pass

    async def _send_single_alert_with_retry(self, url, payload, webhook_name) -> ChannelSendOutcome:
        """发送单条告警（带重试）。失败时回传最后一次尝试的结构化原因。"""
        if not url:
            return channel_failed(ERROR_CHANNEL_NO_TARGET, detail=f"{webhook_name} 未配置 URL")

        # SSRF 防护（发送侧兜底）：路由层已校验，但配置也可能经由通用
        # system-config 接口直写 DB，这里在真正发起请求前再校验一次，
        # 拒绝 http(s) 以外的协议及本地/私网/云元数据端点。
        try:
            await asyncio.to_thread(resolve_webhook_url, url)
        except ValueError as exc:
            logger.error(f"[{self.channel_name}] 拒绝发送告警，Webhook URL 校验失败 [{webhook_name}]: {exc}")
            return channel_failed(ERROR_CHANNEL_URL_REJECTED, detail=str(exc))

        # 先做一次、再按 retries-1 补：避免用占位 outcome 起头，
        # 否则重试次数被配成 0 时会返回一个谁也没产生过的假原因。
        retries = self.max_retries if self.retry_enabled else 1
        outcome = await self._attempt_send(url, payload, webhook_name)
        for attempt in range(1, retries):
            if outcome.ok:
                return outcome
            await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))
            outcome = await self._attempt_send(url, payload, webhook_name)
        return outcome

    async def _attempt_send(self, url, payload, webhook_name) -> ChannelSendOutcome:
        try:
            return await self._send_single_alert(url, payload, webhook_name)
        except Exception as exc:
            logger.error(f"[{self.channel_name}] 发送异常 [{webhook_name}]: {exc}")
            return channel_failed(ERROR_CHANNEL_UNEXPECTED, detail=str(exc))

    async def _send_single_alert(self, url, payload, webhook_name) -> ChannelSendOutcome:
        """发送单条告警（异步）"""
        try:
            endpoint = await asyncio.to_thread(resolve_webhook_url, url)
        except ValueError as exc:
            logger.error(f"[{self.channel_name}] 连接前 SSRF 二次校验失败: {exc}")
            return channel_failed(ERROR_CHANNEL_URL_REJECTED, detail=str(exc))

        try:
            response = await self._post_payload(endpoint, payload)
        except httpx.TimeoutException:
            logger.warning(f"[{self.channel_name}] 告警发送超时: {webhook_name}")
            return channel_failed(ERROR_CHANNEL_TIMEOUT, detail=f"{webhook_name} 请求超时")
        except httpx.RequestError as exc:
            logger.warning(f"[{self.channel_name}] 告警请求异常: {exc}")
            return channel_failed(ERROR_CHANNEL_NETWORK, detail=str(exc))

        outcome = self._evaluate_response(response)
        if not outcome.ok:
            logger.warning(f"[{self.channel_name}] 告警发送失败 [{webhook_name}]: {outcome.describe()}")
        return outcome

    async def _post_payload(self, endpoint, payload):
        verify_ssl = os.getenv("ALERT_VERIFY_SSL", "true").lower() != "false"
        # P1-11：显式 follow_redirects=False，防止服务端 302 到内网/云
        # metadata 端点绕过初始 URL 校验。
        async with httpx.AsyncClient(
            verify=verify_ssl,
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return await client.post(
                endpoint.pinned_http_url(),
                json=payload,
                headers={
                    "Content-Type": "application/json;charset=utf-8",
                    "Host": endpoint.host_header(),
                },
                extensions={"sni_hostname": endpoint.host},
            )

    def _evaluate_response(self, response) -> ChannelSendOutcome:
        """把第三方响应翻译成结构化结果。原文只进 detail，判定只看码。"""
        body = response.text[:RESPONSE_DETAIL_LIMIT]
        if response.status_code != HTTP_OK:
            return channel_failed(ERROR_CHANNEL_HTTP_STATUS, detail=f"HTTP {response.status_code}: {body}")

        try:
            response_data = response.json()
        except ValueError as exc:
            return channel_failed(ERROR_CHANNEL_BAD_RESPONSE, detail=f"{exc}; 原始响应: {body}")

        # 形状校验留在基类：子类的 _check_response 才能是纯判定，不必各自
        # 兜一遍 KeyError/TypeError 再回一句无码的"响应解析失败"。
        if not isinstance(response_data, dict):
            return channel_failed(ERROR_CHANNEL_BAD_RESPONSE, detail=f"响应不是 JSON 对象: {body}")

        success, error_detail = self._check_response(response_data)
        if success:
            return channel_sent()
        return channel_failed(ERROR_CHANNEL_REJECTED, detail=error_detail or None)

    async def send_alert_force(self, message, level) -> ChannelSendOutcome:
        """强制发送告警（手动触发，忽略级别过滤）"""
        return await self._dispatch(message, level, lambda _target_levels: True)

    async def send_alert_for_level(self, message, level, default_levels) -> ChannelSendOutcome:
        """自动发送告警（优先级：Webhook LEVELS > AUTO_ALERT_LEVELS）"""
        allowed_levels = default_levels or []

        def should_send(target_levels):
            return (level in target_levels) if target_levels else (level in allowed_levels)

        return await self._dispatch(message, level, should_send)

    async def _dispatch(self, message, level, should_send) -> ChannelSendOutcome:
        if not self.webhooks:
            return channel_failed(ERROR_CHANNEL_NO_TARGET, detail=f"{self.channel_name} 未配置 Webhook")

        targets = [config for config in self.webhooks if should_send(config.get("levels", []))]
        if not targets:
            return channel_failed(ERROR_CHANNEL_LEVEL_FILTERED, detail=f"没有订阅 {level} 级别的 Webhook")

        payload = self._build_payload(message, level)
        names = [_webhook_name(config) for config in targets]
        outcomes = await asyncio.gather(
            *(
                self._send_single_alert_with_retry(config.get("url", ""), payload, name)
                for config, name in zip(targets, names, strict=True)
            ),
            return_exceptions=True,
        )
        return merge_channel_outcomes(zip(names, outcomes, strict=True))
