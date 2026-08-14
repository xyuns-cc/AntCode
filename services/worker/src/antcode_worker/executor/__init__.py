"""
执行器模块

提供任务执行的各种实现。

本包不做聚合再导出。``rule_network_relay`` 是 Rule 沙箱内 PID 1 的入口，
聚合会经 ``sandbox`` → ``engine`` → Redis 客户端把控制面 ``Settings()``
拉进 relay 的导入链，而沙箱不继承 DATABASE_URL（C1 allowlist），relay 直接
导入失败。调用方一律从具体子模块导入。

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
"""
