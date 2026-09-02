"""Common 模块

通用功能：
- config: 配置管理
- logging: 日志配置
- exceptions: 异常定义
- time: 时间工具
- security: 安全相关（JWT、API Key、mTLS、权限）

本包不做聚合再导出：一旦在这里 ``from ... import settings``，任何
``import antcode_core.common.<子模块>`` 都会连带实例化控制面 ``Settings()``。
Rule 沙箱 relay 与仅依赖 Redis 的清理服务没有控制面配置，会直接导入失败。
调用方一律直接导入具体子模块。
"""
