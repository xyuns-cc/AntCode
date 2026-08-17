"""没有控制面配置的进程必须能导入 Rule 沙箱 relay 与 Spider 清理路径。

``antcode_core.common.config`` 在模块作用域执行 ``settings = Settings()``，
所以任何模块级 ``from antcode_core.common.config import settings``（或任何把
它拖进导入链的聚合式 ``__init__``）都会把"必须有 DATABASE_URL / REDIS_URL"
变成导入期硬约束。两条真实路径没有控制面配置：

- Rule 沙箱里以 PID 1 启动的 ``antcode_worker.executor.rule_network_relay``
  —— ``ProcessExecutor._build_env`` 按 C1 allowlist 刻意不传 secrets；
- e2e / 级联删除只连 Redis 的 Spider 存储清理；
- 生产 compose 的 ``crawl-redis-upgrade`` 服务（``docker-compose.prod.middleware.yml``）
  按最小权限**只挂 Redis secret**，却要跑 ``scripts.check_ready_streams``（runbook §4.1
  的排空门禁）与 ``scripts.migrate_crawl_redis``（``deploy-production.sh`` 的必经步骤）。
  这两条曾经通过 ``common.security`` 聚合包间接拖进 ``settings``，导致 fresh-deploy 与
  existing-upgrade **两种模式都在该步骤崩溃**——真机实测出来的，不是理论风险。

这些用例锁住该边界：谁把 ``settings`` 拉回模块作用域，谁在这里挂。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

CONFIG_FREE_MODULES = (
    "antcode_worker.executor.rule_network_relay",
    "antcode_core.application.services.crawl.spider_storage_cleanup",
    "antcode_core.infrastructure.redis.keys",
    "antcode_core.infrastructure.redis.control_plane",
    "antcode_core.common.logging",
    "antcode_core.common.task_payload_contract",
    "scripts.check_ready_streams",
    "scripts.migrate_crawl_redis",
)
RELAY_MODULE = "antcode_worker.executor.rule_network_relay"
_SUBPROCESS_TIMEOUT_SECONDS = 120


def _run_without_control_plane_config(*args: str) -> subprocess.CompletedProcess[str]:
    """在清空控制面后端配置的子进程里执行 python。

    空字符串会覆盖 ``.env`` 中的取值，等价于容器里根本没有这两个变量；
    两个 backendless 开关显式置 false，把校验固定在控制面分支上，
    避免宿主 ``.env`` 让探针失效。
    """
    env = os.environ | {
        "DATABASE_URL": "",
        "REDIS_URL": "",
        "WORKER_GATEWAY_BACKENDLESS": "false",
        "WORKER_DIRECT_SCOPED_REDIS": "false",
    }
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def test_probe_actually_removes_control_plane_config() -> None:
    """反向对照：同一环境下控制面配置模块必须导入失败。

    没有这条，上面的正向断言可能因为探针没生效而变成空断言。
    """
    result = _run_without_control_plane_config("-c", "import antcode_core.common.config")

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


@pytest.mark.parametrize("module", CONFIG_FREE_MODULES)
def test_module_imports_without_control_plane_config(module: str) -> None:
    result = _run_without_control_plane_config("-c", f"import {module}")

    assert result.returncode == 0, result.stderr


def test_relay_entrypoint_reaches_argument_parsing_without_control_plane_config() -> None:
    """复刻 bwrap 里的 ``python -m`` 启动方式：必须走到参数校验而非导入失败。"""
    result = _run_without_control_plane_config("-m", RELAY_MODULE)

    assert "Rule namespace relay 参数格式错误" in result.stderr
