"""把 cgroup 代际判定指向临时目录的共享搭建。

``resource_budget._require_cgroup_v2`` 读的是真实的 ``/sys/fs/cgroup``。用例不把它
指过来，结果就取决于跑在哪台机器上——开发机 macOS 没有这个目录、CI 容器是 v2、
v1 宿主上则会抛——那正是"验证选错对象"的形状：同一份断言在不同机器上验的是不同
的东西。四个用例文件共用这一份，避免各抄一遍再各自分叉。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker import resource_budget

# 真机实测值（192.168.1.250，Ubuntu 22.04 / 内核 5.15 / cgroup v2 统一层级）。
_V2_CONTROLLERS_BODY = "cpuset cpu io memory hugetlb pids rdma misc"


def simulate_cgroup_v2_host(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """让代际判定看到一个 cgroup v2 统一层级宿主。"""
    controllers = root / "cgroup.controllers"
    controllers.write_text(_V2_CONTROLLERS_BODY, encoding="utf-8")
    monkeypatch.setattr(resource_budget, "CGROUP_ROOT", root)
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CONTROLLERS", controllers)
