"""Common 模块

本包不做聚合再导出：一旦在这里 ``from ... import settings``，任何
``import antcode_core.common.<子模块>`` 都会连带实例化控制面 ``Settings()``。
Rule 沙箱 relay 与仅依赖 Redis 的清理服务没有控制面配置，会直接导入失败。
调用方一律直接导入具体子模块。
"""
