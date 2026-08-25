"""Worker 上报的核数与内存必须是**容器额度**，不是宿主 /proc 视图。

资源页(GET /workers/{id}/resources)、Monitor 详情抽屉、调度器可用性门禁与 Worker 的
/metrics 端点读的都是这几个上报值。报宿主数字会让容量规划高估一个数量级——真机
实测同一台 Worker：宿主 8 核 / 31.34GiB，容器额度 2 核 / 3GiB。

**这些 stub 能证明什么、不能证明什么**：monkeypatch 把 cgroup 路径指到 tmp_path，
只能证明"读到这些内容时算出什么值"。它证明不了容器里 ``/sys/fs/cgroup/memory.max``
真的存在且真的是本容器的额度——那一条只能真机验(见提交说明的实测记录)。

证伪方式：把 ``metric_probes.probe_cpu`` 的 ``resolve_cpu_budget(...)`` 换回
``psutil.cpu_count()``、把 ``probe_memory`` 换回直接读 ``psutil.virtual_memory()``，
除了标注为对照组的两条以外全部变红。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker import cpu_usage, resource_budget, resource_usage
from antcode_worker.cpu_usage import ContainerCpuSampler
from antcode_worker.heartbeat import metric_probes
from antcode_worker.heartbeat.metrics_assembly import MIB_IN_BYTES, collect_metrics
from antcode_worker.resource_budget import ResourceBudgetError
from antcode_worker.resource_usage import HostMemory

from tests.unit.worker.cgroup_v2_support import simulate_cgroup_v2_host

# 真机实测值（192.168.1.250 上的 antcode-mn-worker3）
_CONTAINER_MEMORY_BYTES = 3221225472
_CONTAINER_MEMORY_MB = 3072.0
_CONTAINER_CPU_MAX = "200000 100000"
_CONTAINER_CPU_CORES = 2
_CGROUP_MEMORY_CURRENT = 70201344
_CGROUP_INACTIVE_FILE = 4096

_HOST_MEMORY_BYTES = 33651208192
_HOST_MEMORY_MB = round(_HOST_MEMORY_BYTES / MIB_IN_BYTES, 1)
_HOST_CPU_COUNT = 8
_HOST_USED_BYTES = 12 * 1024 * MIB_IN_BYTES
_HOST_AVAILABLE_BYTES = 20 * 1024 * MIB_IN_BYTES
_HOST_PERCENT = 38.5

# working set 口径：memory.current 扣掉内核随时可回收的 inactive_file
_EXPECTED_USED_BYTES = _CGROUP_MEMORY_CURRENT - _CGROUP_INACTIVE_FILE
_EXPECTED_USED_MB = round(_EXPECTED_USED_BYTES / MIB_IN_BYTES, 1)
_EXPECTED_AVAILABLE_MB = round((_CONTAINER_MEMORY_BYTES - _EXPECTED_USED_BYTES) / MIB_IN_BYTES, 1)
_EXPECTED_PERCENT = round(_EXPECTED_USED_BYTES * 100.0 / _CONTAINER_MEMORY_BYTES, 1)
# 心跳 wire 只带 MB 精度的已用量（metrics_assembly 由 used_mb 反算字节）。
_EXPECTED_USED_WIRE_BYTES = int(_EXPECTED_USED_MB * MIB_IN_BYTES)

_MEMORY_STAT_BODY = f"anon 67047424\nfile 16384\ninactive_file {_CGROUP_INACTIVE_FILE}\nactive_file 0\n"

_FALLBACK_CONCURRENCY = 4


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _host_snapshot() -> HostMemory:
    return HostMemory(
        total_bytes=_HOST_MEMORY_BYTES,
        used_bytes=_HOST_USED_BYTES,
        available_bytes=_HOST_AVAILABLE_BYTES,
        percent=_HOST_PERCENT,
    )


@pytest.fixture
def host_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主快照固定成真机数量级，好让"报了宿主值"一眼可辨。"""
    monkeypatch.setattr(metric_probes, "_host_memory", _host_snapshot)


def _point_memory_cgroup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    limit: str | None = str(_CONTAINER_MEMORY_BYTES),
    current: str | None = str(_CGROUP_MEMORY_CURRENT),
    stat: str | None = _MEMORY_STAT_BODY,
) -> None:
    """把三个 v2 控制文件指到 tmp_path；传 None 表示该文件不存在。"""
    absent = tmp_path / "absent"
    simulate_cgroup_v2_host(monkeypatch, tmp_path)
    monkeypatch.setattr(
        resource_budget,
        "CGROUP_V2_MEMORY_MAX",
        _write(tmp_path / "memory.max", limit) if limit is not None else absent,
    )
    monkeypatch.setattr(
        resource_usage,
        "CGROUP_V2_MEMORY_CURRENT",
        _write(tmp_path / "memory.current", current) if current is not None else absent,
    )
    monkeypatch.setattr(
        resource_usage,
        "CGROUP_V2_MEMORY_STAT",
        _write(tmp_path / "memory.stat", stat) if stat is not None else absent,
    )


def _point_cpu_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, quota: str | None) -> None:
    absent = tmp_path / "absent-cpu"
    simulate_cgroup_v2_host(monkeypatch, tmp_path)
    monkeypatch.setattr(
        resource_budget,
        "CGROUP_V2_CPU_MAX",
        _write(tmp_path / "cpu.max", quota) if quota is not None else absent,
    )
    # 额度判成 cgroup 后使用率必须从同层读，所以用量文件要一起指过来。
    monkeypatch.setattr(
        cpu_usage,
        "CGROUP_V2_CPU_STAT",
        _write(tmp_path / "cpu.stat", "usage_usec 0\nnr_periods 0\n"),
    )
    monkeypatch.setattr(metric_probes.psutil, "cpu_count", lambda: _HOST_CPU_COUNT)


@pytest.mark.asyncio
async def test_reported_cpu_cores_is_container_quota_not_host_nproc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核数报容器 CPU 配额；报 nproc 就是资源页高估的那个数。"""
    _point_cpu_cgroup(monkeypatch, tmp_path, quota=_CONTAINER_CPU_MAX)

    metrics = await metric_probes.probe_cpu(ContainerCpuSampler())

    assert metrics.count == _CONTAINER_CPU_CORES
    assert metrics.count != _HOST_CPU_COUNT, "读到宿主核数说明 cgroup 配额没被采信"


@pytest.mark.asyncio
async def test_reported_memory_total_is_container_limit_not_host_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """总内存报容器 mem_limit；报宿主 31.34GiB 会让容量规划高估 10 倍。"""
    _point_memory_cgroup(monkeypatch, tmp_path)

    metrics = await metric_probes.probe_memory()

    assert metrics.total_mb == _CONTAINER_MEMORY_MB
    assert metrics.total_mb != _HOST_MEMORY_MB, "读到宿主总内存说明 cgroup 上限没被采信"


@pytest.mark.asyncio
async def test_reported_memory_used_comes_from_the_same_cgroup_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """已用必须与总额同层：总额取容器、已用取宿主会算出"已用 12GiB / 总计 3GiB"。"""
    _point_memory_cgroup(monkeypatch, tmp_path)

    metrics = await metric_probes.probe_memory()

    assert metrics.used_mb == _EXPECTED_USED_MB
    assert metrics.used_mb < metrics.total_mb, "已用超过总额说明两个数不同源"


@pytest.mark.asyncio
async def test_reported_memory_available_and_percent_are_derived_from_container_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """可用与占比是资源页圆环和调度门禁读的值，必须同样落在容器口径上。"""
    _point_memory_cgroup(monkeypatch, tmp_path)

    metrics = await metric_probes.probe_memory()

    assert metrics.available_mb == _EXPECTED_AVAILABLE_MB
    assert metrics.percent == _EXPECTED_PERCENT
    assert metrics.percent != _HOST_PERCENT, "占比仍是宿主忙闲程度，调度门禁会按宿主判可用性"


@pytest.mark.asyncio
async def test_heartbeat_wire_fields_carry_container_numbers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """一路证到心跳 wire 形状：资源页读的就是这两个字段。"""
    _point_memory_cgroup(monkeypatch, tmp_path)
    _point_cpu_cgroup(monkeypatch, tmp_path, quota=_CONTAINER_CPU_MAX)
    from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

    metrics = await collect_metrics(SystemMetricsCollector(), _FALLBACK_CONCURRENCY)

    assert metrics.cpu_cores == _CONTAINER_CPU_CORES
    assert metrics.memory_total_bytes == _CONTAINER_MEMORY_BYTES
    assert metrics.memory_used_bytes == _EXPECTED_USED_WIRE_BYTES


@pytest.mark.asyncio
async def test_missing_cgroup_usage_file_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """额度判为 cgroup 却读不到同层占用 = 两层对不上，必须抛，不许退回宿主占用。"""
    _point_memory_cgroup(monkeypatch, tmp_path, current=None)

    with pytest.raises(ResourceBudgetError, match="额度与占用必须同源"):
        await metric_probes.probe_memory()


@pytest.mark.asyncio
async def test_memory_stat_without_reclaimable_key_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """算不出 working set 就明确失败，不许改用会虚高的 memory.current 原值。"""
    _point_memory_cgroup(monkeypatch, tmp_path, stat="anon 67047424\nfile 16384\n")

    with pytest.raises(ResourceBudgetError, match="working set"):
        await metric_probes.probe_memory()


@pytest.mark.asyncio
async def test_unparseable_cgroup_usage_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    _point_memory_cgroup(monkeypatch, tmp_path, current="不是数字")

    with pytest.raises(ResourceBudgetError, match="拒绝按宿主占用估算"):
        await metric_probes.probe_memory()


@pytest.mark.asyncio
async def test_unparseable_cpu_quota_is_not_swallowed_by_the_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配额解析失败必须冒出探针，不能被 ``probe_cpu`` 兜 psutil 抖动的 except 吞掉。

    吞掉的表现是核数悄悄变 0 或退回宿主值——正是本次要消灭的静默失败。
    """
    _point_cpu_cgroup(monkeypatch, tmp_path, quota="garbage")

    with pytest.raises(ResourceBudgetError, match="拒绝按宿主核数估算"):
        await metric_probes.probe_cpu(ContainerCpuSampler())


@pytest.mark.asyncio
async def test_bare_metal_without_cgroup_reports_host_view_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_memory: None,
) -> None:
    """对照组（非证伪项）：没有 cgroup 上限 = 裸机，宿主视图就是正确答案。

    修复前后都应该绿。它存在的意义是证明上面几条不是"永远返回容器常数"——
    真的会随来源改变，而不是把宿主分支也一起写死。
    """
    _point_memory_cgroup(monkeypatch, tmp_path, limit=None, current=None, stat=None)

    metrics = await metric_probes.probe_memory()

    assert metrics.total_mb == _HOST_MEMORY_MB
    assert metrics.percent == _HOST_PERCENT


@pytest.mark.asyncio
async def test_cpu_without_cgroup_quota_reports_host_cores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组（非证伪项）：没有 CPU 配额时宿主核数就是正确答案。"""
    _point_cpu_cgroup(monkeypatch, tmp_path, quota=None)

    metrics = await metric_probes.probe_cpu(ContainerCpuSampler())

    assert metrics.count == _HOST_CPU_COUNT
