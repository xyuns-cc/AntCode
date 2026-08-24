"""Gateway TLS 材料的到期监控。

``tls_material`` 解决的是"证书换不了"——改盘即生效、不重启。它解决不了另一半：
**没人知道快到期了**。材料不变就不会触发热更新回调，一张还有三天到期的证书在
盘上安安静静躺着，Gateway 一条日志都不会出，直到某天全队 Worker 同时握手失败。

**形态选型**（跟着仓内既有设施走，不自创第三种）：

- Prometheus：`prometheus_client` 只在 web_api 里，`/metrics` 也只有它有；
  ``infra/`` 里没有任何 Prometheus / Grafana 采集端。给 Gateway 单开一个没人
  抓取的 exporter 等于加一个死依赖。
- 告警渠道：``alert_service`` 在 core，靠 DB 里的告警配置驱动，Gateway 进程
  当前完全不碰它；为一条证书告警把控制面依赖引进数据面服务不划算。
- ``audit:security`` Stream：语义是"凭据被拒事件"，供上层做爆破检测，不是健康度。
- ``/health/ready``：响应体是"依赖此刻可不可用"，且 compose 的存活探针直接读
  ``"grpc":"ok"`` 片段（docker-compose.prod.control.yml）。把"还有 20 天到期"
  塞进去只有两种结果：要么不影响状态码（那它就不是 readiness），要么让 Gateway
  变 unhealthy 把 Worker 一起拖下水——而重启并不能让证书变新。

于是落在 Gateway 唯一真正有人看的通道上：**带结构化码的分级日志**，与
``tls_material`` 的失败码同一套语汇，运维 grep 一个前缀就能拿到全部证书事件。

**每个 tick 必留痕**：正常也打一条 INFO。否则"监控本身死了"与"一切正常"在日志里
长得一模一样。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from antcode_core.common.exceptions import ConfigurationError
from cryptography import x509
from loguru import logger

from antcode_gateway.tls_material import TlsMaterialPaths

# 结构化码；与 tls_material 的失败码同前缀，测试一律匹配常量而非中文描述。
TLS_MATERIAL_EXPIRY_OK = "TLS_MATERIAL_EXPIRY_OK"
TLS_MATERIAL_EXPIRING = "TLS_MATERIAL_EXPIRING"
TLS_MATERIAL_EXPIRED = "TLS_MATERIAL_EXPIRED"
#: 读不出/解析不出有效期不是"没问题"，是**不知道**——按失败上报，绝不静默放过。
TLS_MATERIAL_EXPIRY_UNREADABLE = "TLS_MATERIAL_EXPIRY_UNREADABLE"

SECONDS_PER_DAY = 86_400
#: 30 天：够签发新证书 + 走一轮变更审批，也短到不会天天刷屏。
DEFAULT_TLS_EXPIRY_WARNING_DAYS = 30
#: 1 小时：证书有效期以天计，更密的采样只会让日志更吵，换不来任何提前量。
DEFAULT_TLS_EXPIRY_CHECK_INTERVAL_SECONDS = 3_600


@dataclass(frozen=True, slots=True)
class TlsExpiryPolicy:
    """到期监控的两个阈值，均由 ``GatewayConfig`` 从环境变量注入。"""

    warning_seconds: int
    interval_seconds: int

    def __post_init__(self) -> None:
        if self.warning_seconds <= 0 or self.interval_seconds <= 0:
            raise ValueError("TLS 到期监控的预警窗口与检查间隔必须为正")


@dataclass(frozen=True, slots=True)
class ExpiryObservation:
    """一次观测里**最早**到期的那张证书。"""

    subject: str
    not_valid_after: datetime
    remaining_seconds: int


def _load_certificates(path: Path) -> list[x509.Certificate]:
    try:
        return x509.load_pem_x509_certificates(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"{path.name}: {exc}") from exc


def earliest_expiry(paths: TlsMaterialPaths, now: datetime) -> ExpiryObservation:
    """服务端证书链与客户端 CA bundle 里最早到期的那张。

    CA 一起看：CA 过期会让**每个** Worker 的证书校验一起失败，比服务端叶子证书
    到期更致命，而它的有效期通常长到没人记得去查。
    """
    sources = [paths.certificate] if paths.client_ca is None else [paths.certificate, paths.client_ca]
    certificates = [item for path in sources for item in _load_certificates(path)]
    if not certificates:
        raise ConfigurationError("TLS 材料里没有任何 PEM 证书")
    soonest = min(certificates, key=lambda item: item.not_valid_after_utc)
    return ExpiryObservation(
        subject=soonest.subject.rfc4514_string(),
        not_valid_after=soonest.not_valid_after_utc,
        remaining_seconds=int((soonest.not_valid_after_utc - now).total_seconds()),
    )


def _report(observation: ExpiryObservation, policy: TlsExpiryPolicy) -> str:
    remaining_days = observation.remaining_seconds / SECONDS_PER_DAY
    if observation.remaining_seconds <= 0:
        logger.error(
            "{}: TLS 材料已过期 subject={} not_after={}",
            TLS_MATERIAL_EXPIRED,
            observation.subject,
            observation.not_valid_after.isoformat(),
        )
        return TLS_MATERIAL_EXPIRED
    if observation.remaining_seconds <= policy.warning_seconds:
        logger.warning(
            "{}: TLS 材料即将到期 subject={} not_after={} remaining_days={:.2f}",
            TLS_MATERIAL_EXPIRING,
            observation.subject,
            observation.not_valid_after.isoformat(),
            remaining_days,
        )
        return TLS_MATERIAL_EXPIRING
    logger.info(
        "{}: TLS 材料有效期充足 subject={} not_after={} remaining_days={:.2f}",
        TLS_MATERIAL_EXPIRY_OK,
        observation.subject,
        observation.not_valid_after.isoformat(),
        remaining_days,
    )
    return TLS_MATERIAL_EXPIRY_OK


class TlsExpiryMonitor:
    """周期性读盘判有效期。

    **不复用 ``TlsMaterialLoader``**：``load()`` 会顺手刷新它的内容指纹，监控每小时
    刷一次，gRPC 的握手回调就会认为"材料没变"，热更新从此永久失效。监控只读自己的，
    绝不碰热更新链路上的状态。
    """

    def __init__(self, paths: TlsMaterialPaths, policy: TlsExpiryPolicy) -> None:
        self._paths = paths
        self._policy = policy

    def check_once(self) -> str:
        """跑一次检查并落一条日志，返回本次的结构化码。"""
        try:
            observation = earliest_expiry(self._paths, datetime.now(UTC))
        except ConfigurationError as exc:
            logger.error("{}: 无法判定 TLS 材料有效期: {}", TLS_MATERIAL_EXPIRY_UNREADABLE, exc)
            return TLS_MATERIAL_EXPIRY_UNREADABLE
        return _report(observation, self._policy)

    async def run(self) -> None:
        """先查再睡：启动即出一条读数，不用等满一个间隔才知道证书状态。"""
        while True:
            self.check_once()
            await asyncio.sleep(self._policy.interval_seconds)


__all__ = [
    "DEFAULT_TLS_EXPIRY_CHECK_INTERVAL_SECONDS",
    "DEFAULT_TLS_EXPIRY_WARNING_DAYS",
    "SECONDS_PER_DAY",
    "TLS_MATERIAL_EXPIRED",
    "TLS_MATERIAL_EXPIRING",
    "TLS_MATERIAL_EXPIRY_OK",
    "TLS_MATERIAL_EXPIRY_UNREADABLE",
    "ExpiryObservation",
    "TlsExpiryMonitor",
    "TlsExpiryPolicy",
    "earliest_expiry",
]
