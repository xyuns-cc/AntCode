"""Worker 能力检测。

从 ``reporter.py`` 拆出：能力检测与"心跳/租约续期"是两件事，
放在一起会让续期主循环所在的文件继续膨胀。
"""

from __future__ import annotations

from antcode_contracts.wire_contract import wire_contract_capability
from loguru import logger


class CapabilityDetector:
    """
    Worker能力检测器

    检测本地环境的可选能力并上报给主控。
    """

    def __init__(self):
        self._cached_capabilities: dict | None = None

    def detect_all(
        self,
        force_refresh: bool = False,
        task_types: list[str] | None = None,
    ) -> dict:
        """检测所有能力。

        Args:
            force_refresh: 忽略缓存重新检测。
            task_types: T6-T4a: 当前 worker 注册的 plugin 名列表（"code"/
                "rule"/"spider"/"render" 等），供 master dispatcher 按
                能力路由，避免把 rule 派到关掉 RulePlugin 的 worker。
                None 表示保持原缓存值不动。
        """
        if self._cached_capabilities and not force_refresh and task_types is None:
            return self._cached_capabilities

        capabilities = self._cached_capabilities or {}
        # 首次或强制刷新时才检测底层依赖。wire_contract 是**声明**不是检测：控制面
        # 在签发 Lease 前比对它，缺失即拒发，所以它必须出现在每一份快照里——注册、
        # 初始 Lease、每一次续租走的都是这一个入口。
        if not capabilities or force_refresh:
            capabilities = {"curl_cffi": self._detect_curl_cffi(), **wire_contract_capability()}

        # T6-T4a: task_types 覆盖式更新（每次 register 都传最新的）
        if task_types is not None:
            capabilities["task_types"] = list(task_types)

        self._cached_capabilities = capabilities
        logger.debug(f"Worker能力检测完成: {self._summarize(capabilities)}")
        return capabilities

    def _summarize(self, capabilities: dict) -> str:
        """生成能力摘要"""
        enabled = []
        for name, cap in capabilities.items():
            # T6-T4a: task_types 是 list[str]，其他是 {"enabled": bool}
            if name == "task_types":
                if cap:
                    enabled.append(f"tasks=[{','.join(cap)}]")
                continue
            if isinstance(cap, dict) and cap.get("enabled"):
                enabled.append(name)
        return ", ".join(enabled) if enabled else "无额外能力"

    def _detect_curl_cffi(self) -> dict:
        """检测 curl_cffi 能力"""
        result = {"enabled": False}

        try:
            from curl_cffi import requests as curl_requests  # noqa: F401

            result["enabled"] = True
        except ImportError:
            pass

        return result


# 全局能力检测器实例
_capability_detector: CapabilityDetector | None = None


def get_capability_detector() -> CapabilityDetector:
    """获取全局能力检测器"""
    global _capability_detector
    if _capability_detector is None:
        _capability_detector = CapabilityDetector()
    return _capability_detector


__all__ = ["CapabilityDetector", "get_capability_detector"]
