"""调用期解析全局 ``Settings``，不在导入期绑定控制面配置。

``antcode_core.common.config`` 在模块作用域执行 ``settings = Settings()``，
所以任何模块级 ``from antcode_core.common.config import settings`` 都会把
"必须有 DATABASE_URL / REDIS_URL" 变成导入期硬约束。两条真实路径没有控制面
配置，却必须能导入：

- Rule 沙箱内以 PID 1 启动的 ``antcode_worker.executor.rule_network_relay``
  （沙箱按 C1 allowlist 刻意不继承宿主 worker 的 secrets）；
- 只连 Redis 的 Spider 存储清理路径。

基础设施与日志等被广泛导入的模块一律改用本模块的 ``current_settings()``：
配置校验仍然会失败并抛错，只是推迟到真正读配置的那一刻，不做任何降级。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcode_core.common.config import Settings

__all__ = ["current_settings"]


def current_settings() -> "Settings":
    """返回全局 ``Settings`` 单例；缺配置时按原样抛出校验错误。"""
    from antcode_core.common.config import settings

    return settings
