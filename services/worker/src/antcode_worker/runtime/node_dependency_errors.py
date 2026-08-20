"""Node 依赖装配的结构化失败码。

单独成模块的原因与 ``alert_delivery_status`` 一致：策略层、插件层、测试三处都要
表达"这次装配为什么没成"，散成字面量后调用方只能去匹配中文文案，契约一改就漂。

两条线严格分开：``error_code`` 是程序唯一可判定的契约，``detail`` 装给人看的原文
（具体文件名 / URL / 包管理器 stderr）。**禁止**对 ``detail`` 做任何匹配分支——
仓里已有 ``"NOSCRIPT" in str(exc)`` 这种字符串契约漂成死代码的 P0 前科。
"""

from typing import Final

# 元数据 / 清单本身不可信
NODE_DEP_UNTRUSTED_CONFIG: Final = "NODE_DEP_UNTRUSTED_CONFIG"
NODE_DEP_MANIFEST_REJECTED: Final = "NODE_DEP_MANIFEST_REJECTED"
NODE_DEP_LOCKFILE_REJECTED: Final = "NODE_DEP_LOCKFILE_REJECTED"
# lockfile 里的依赖来源不合法：本地路径 / git / 非 https 协议
NODE_DEP_SOURCE_REJECTED: Final = "NODE_DEP_SOURCE_REJECTED"
# 依赖来源是 https，但主机不在 registry 白名单内
NODE_DEP_REGISTRY_REJECTED: Final = "NODE_DEP_REGISTRY_REJECTED"
NODE_DEP_REGISTRY_MISCONFIGURED: Final = "NODE_DEP_REGISTRY_MISCONFIGURED"
# 装配前置条件不满足
NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED: Final = "NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED"
NODE_DEP_LOCKFILE_MISSING: Final = "NODE_DEP_LOCKFILE_MISSING"
NODE_DEP_OFFLINE_CACHE_MISSING: Final = "NODE_DEP_OFFLINE_CACHE_MISSING"
NODE_DEP_INSTALL_FAILED: Final = "NODE_DEP_INSTALL_FAILED"
# TypeScript 入口找不到 runner
NODE_DEP_TS_RUNNER_MISSING: Final = "NODE_DEP_TS_RUNNER_MISSING"


class NodeDependencyError(Exception):
    """带结构化码的 Node 依赖失败。``str()`` 渲染成 ``码: 原文``。"""

    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


class NodeDependencyRejected(NodeDependencyError, ValueError):
    """校验期拒绝。继承 ValueError，保持既有调用方的异常语义。"""


class NodeDependencyInstallError(NodeDependencyError, RuntimeError):
    """装配期失败。继承 RuntimeError，保持既有调用方的异常语义。"""


__all__ = [
    "NODE_DEP_INSTALL_FAILED",
    "NODE_DEP_LOCKFILE_MISSING",
    "NODE_DEP_LOCKFILE_REJECTED",
    "NODE_DEP_MANIFEST_REJECTED",
    "NODE_DEP_OFFLINE_CACHE_MISSING",
    "NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED",
    "NODE_DEP_REGISTRY_MISCONFIGURED",
    "NODE_DEP_REGISTRY_REJECTED",
    "NODE_DEP_SOURCE_REJECTED",
    "NODE_DEP_TS_RUNNER_MISSING",
    "NODE_DEP_UNTRUSTED_CONFIG",
    "NodeDependencyError",
    "NodeDependencyInstallError",
    "NodeDependencyRejected",
]
