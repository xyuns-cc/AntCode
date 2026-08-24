"""Worker 上报的 CPU 使用率必须是**本容器配额**的占用，不是宿主整机的忙闲程度。

这个值不是只拿来显示的：``WorkerLoadBalancer.is_worker_available`` 拿它当硬门禁
(阈值 90)、``calculate_load_score`` 给它三分之一权重(三项判据之一)。宿主口径
下两个方向都会判错，真机 192.168.1.250 实测(``docker exec`` 直接读 cgroup)：

- 漏判：``antcode-mn-worker2`` 打满自己 2 核配额时容器口径 99.6%、内核已在限流
  (``nr_throttled`` 0→85、``throttled_usec`` 0→8.58s)，宿主口径只有 36.0%;
- 误判：同一时刻真正空闲的 ``antcode-worker`` / ``antcode-mn-worker3``(容器 0.1% /
  0.4%)被记在同一个 36.0% 名下。三台 Worker 报同一个数字，本身就说明这个值与
  "哪台 Worker"无关。

**这些 stub 能证明什么、不能证明什么**：monkeypatch 把 cgroup 路径指到 tmp_path、
把时钟换成固定步进，只能证明"读到这些内容、经过这么长的窗口时算出什么值"。它证明
不了容器里 ``/sys/fs/cgroup/cpu.stat`` 真的存在、``usage_usec`` 真的只计本容器——
那一条只能真机验(见提交说明的实测记录)。

证伪方式：把 ``ContainerCpuSampler.sample`` 的 cgroup 分支删掉、让它无条件返回
``host_percent``，除了标注为对照组的两条以外全部变红。
"""

from __future__ import annotations

from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerLoadBalancer
from antcode_core.application.services.workers.worker_metrics import normalize_worker_metrics
from antcode_core.domain.models import WorkerStatus
from antcode_worker import cpu_usage, resource_budget
from antcode_worker.cpu_usage import ContainerCpuSampler
from antcode_worker.heartbeat import metric_probes
from antcode_worker.resource_budget import ResourceBudgetError, resolve_cpu_budget

# 真机实测值（antcode-mn-worker2：cpu.max="200000 100000"，宿主 nproc=8）
_CONTAINER_CPU_MAX = "200000 100000"
_CONTAINER_CORES = 2.0
_HOST_CPU_COUNT = 8
# 加压窗口内的实测两侧数字
_MEASURED_CONTAINER_PERCENT = 99.6
_MEASURED_HOST_PERCENT = 36.0
# 空载基线：宿主 27.8% 全部来自别人的容器，本容器只有 0.4%
_IDLE_CONTAINER_PERCENT = 0.4

_WINDOW_SEC = 6.0
_USEC_PER_SEC = 1_000_000
_CLOCK_ORIGIN = 1000.0

# --cpus=1.5：取整成 1 核当分母会把 100% 的负载报成 150%
_FRACTIONAL_CPU_MAX = "150000 100000"
_FRACTIONAL_CORES = 1.5
_FULL_LOAD_PERCENT = 100.0
_FRACTIONAL_CORES_FLOORED = 1

_SCHEDULER_CPU_GATE = WorkerLoadBalancer.MAX_CPU_THRESHOLD


def _usec_for(percent: float, cores: float) -> int:
    """在 ``_WINDOW_SEC`` 的窗口里跑出这个占用率需要多少微秒 CPU 时间。"""
    return int(percent / 100.0 * _WINDOW_SEC * cores * _USEC_PER_SEC)


class _CpuStatFile:
    """一份可推进的 ``cpu.stat``，模拟累计计数器往前走。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._usage_usec = 0
        self._write()

    def _write(self) -> None:
        body = f"usage_usec {self._usage_usec}\nuser_usec 0\nsystem_usec 0\nnr_periods 0\n"
        self._path.write_text(body, encoding="utf-8")

    def advance(self, usec: int) -> None:
        self._usage_usec += usec
        self._write()


@pytest.fixture
def stepping_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次读表前进一个固定窗口，让百分比可复算。"""
    ticks = count(_CLOCK_ORIGIN, _WINDOW_SEC)
    monkeypatch.setattr(cpu_usage, "time", SimpleNamespace(monotonic=lambda: next(ticks)))


def _point_cpu_cgroup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    quota: str | None = _CONTAINER_CPU_MAX,
    stat_present: bool = True,
    stat_body: str | None = None,
) -> _CpuStatFile | None:
    """把 v2 的 cpu.max / cpu.stat 指到 tmp_path；返回可推进的用量文件。"""
    absent = tmp_path / "absent-cpu"
    cpu_max = _write(tmp_path / "cpu.max", quota) if quota is not None else absent
    monkeypatch.setattr(resource_budget, "CGROUP_V2_CPU_MAX", cpu_max)
    monkeypatch.setattr(resource_budget, "CGROUP_V1_CPU_QUOTA", absent)
    monkeypatch.setattr(cpu_usage, "CGROUP_V1_CPUACCT_USAGE", absent)
    if not stat_present:
        monkeypatch.setattr(cpu_usage, "CGROUP_V2_CPU_STAT", absent)
        return None
    stat_path = tmp_path / "cpu.stat"
    monkeypatch.setattr(cpu_usage, "CGROUP_V2_CPU_STAT", stat_path)
    if stat_body is not None:
        _write(stat_path, stat_body)
        return None
    return _CpuStatFile(stat_path)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _budget():
    return resolve_cpu_budget(_HOST_CPU_COUNT)


def _percent_after(sampler: ContainerCpuSampler, stat: _CpuStatFile, usec: int) -> float:
    """先建立窗口起点，再推进用量，取第二次采样的结果。"""
    sampler.sample(_budget(), _MEASURED_HOST_PERCENT)
    stat.advance(usec)
    return sampler.sample(_budget(), _MEASURED_HOST_PERCENT).percent


def test_saturated_container_is_reported_at_its_own_quota_not_the_host_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stepping_clock: None,
) -> None:
    """打满 2 核配额的容器必须报 99.6，不是宿主那个 36.0。"""
    stat = _point_cpu_cgroup(monkeypatch, tmp_path)
    assert stat is not None

    percent = _percent_after(
        sampler=ContainerCpuSampler(), stat=stat, usec=_usec_for(_MEASURED_CONTAINER_PERCENT, _CONTAINER_CORES)
    )

    assert percent == _MEASURED_CONTAINER_PERCENT
    assert percent != _MEASURED_HOST_PERCENT, "报了宿主使用率说明 cgroup 用量没被采信"


def test_idle_container_is_not_charged_for_a_noisy_neighbour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stepping_clock: None,
) -> None:
    """本容器空闲、宿主被别人打满时，报的必须是本容器的 0.4。"""
    stat = _point_cpu_cgroup(monkeypatch, tmp_path)
    assert stat is not None

    percent = _percent_after(
        sampler=ContainerCpuSampler(),
        stat=stat,
        usec=_usec_for(_IDLE_CONTAINER_PERCENT, _CONTAINER_CORES),
    )

    assert percent == _IDLE_CONTAINER_PERCENT


def test_fractional_quota_is_the_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stepping_clock: None,
) -> None:
    """``--cpus=1.5`` 打满就是 100%；用取整后的 1 核做分母会报成 150%。"""
    stat = _point_cpu_cgroup(monkeypatch, tmp_path, quota=_FRACTIONAL_CPU_MAX)
    assert stat is not None
    full_load_usec = _usec_for(_FULL_LOAD_PERCENT, _FRACTIONAL_CORES)

    percent = _percent_after(sampler=ContainerCpuSampler(), stat=stat, usec=full_load_usec)

    assert percent == _FULL_LOAD_PERCENT
    assert resolve_cpu_budget(_HOST_CPU_COUNT).cores == _FRACTIONAL_CORES_FLOORED, (
        "并发仍按整核定尺寸，两个字段各司其职"
    )


@pytest.mark.asyncio
async def test_probe_cpu_discards_the_host_percent_when_a_quota_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stepping_clock: None,
) -> None:
    """一路证到探针：``probe_cpu`` 读到的 psutil 整机值不得漏进上报字段。"""
    stat = _point_cpu_cgroup(monkeypatch, tmp_path)
    assert stat is not None
    monkeypatch.setattr(metric_probes.psutil, "cpu_count", lambda: _HOST_CPU_COUNT)
    monkeypatch.setattr(metric_probes.psutil, "cpu_percent", lambda interval=None: _MEASURED_HOST_PERCENT)
    sampler = ContainerCpuSampler()

    await metric_probes.probe_cpu(sampler)
    stat.advance(_usec_for(_MEASURED_CONTAINER_PERCENT, _CONTAINER_CORES))
    metrics = await metric_probes.probe_cpu(sampler)

    assert metrics.percent == _MEASURED_CONTAINER_PERCENT
    assert metrics.percent != _MEASURED_HOST_PERCENT


def _online_worker():
    return SimpleNamespace(id="w-1", name="antcode-mn-worker2", status=WorkerStatus.ONLINE)


def _gate_verdict(cpu_percent: float) -> bool:
    """走真实归一化 + 真实门禁，不复制阈值逻辑。"""
    metrics = normalize_worker_metrics(
        {"cpu_percent": cpu_percent, "memory_percent": 10.0, "running_tasks": 0, "max_concurrent_tasks": 4}
    )
    return WorkerLoadBalancer().is_worker_available(_online_worker(), metrics)


def test_scheduler_gate_flips_on_which_cpu_number_it_is_fed() -> None:
    """这就是混用口径的代价：同一时刻两个数给出相反的调度结论。

    **非证伪项**：它刻画的是 ``is_worker_available`` 这条判据本身，不经过采样器，
    所以把本次修复整个退掉它也是绿的。它回答的是另一个问题——"口径错了会怎样"，
    没有它，上面那些"数值对不对"的断言就只是在比大小。
    """
    assert _MEASURED_CONTAINER_PERCENT >= _SCHEDULER_CPU_GATE > _MEASURED_HOST_PERCENT

    assert _gate_verdict(_MEASURED_CONTAINER_PERCENT) is False, "容器已被内核限流，必须停止派活"
    assert _gate_verdict(_MEASURED_HOST_PERCENT) is True, "宿主口径看不见这台已经打满的容器"


def test_scheduler_gate_evicts_an_idle_container_on_the_host_number() -> None:
    """反方向：吵闹邻居把宿主推过阈值，本容器空闲却被挤出候选。

    **非证伪项**，理由同上。
    """
    noisy_host_percent = 95.0
    assert noisy_host_percent >= _SCHEDULER_CPU_GATE > _IDLE_CONTAINER_PERCENT

    assert _gate_verdict(noisy_host_percent) is False
    assert _gate_verdict(_IDLE_CONTAINER_PERCENT) is True


def test_missing_cpu_stat_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """额度判为 cgroup 却读不到同层用量 = 两层对不上，必须抛，不许退回宿主使用率。"""
    _point_cpu_cgroup(monkeypatch, tmp_path, stat_present=False)

    with pytest.raises(ResourceBudgetError, match="额度与用量必须同源"):
        ContainerCpuSampler().sample(_budget(), _MEASURED_HOST_PERCENT)


def test_cpu_stat_without_usage_key_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_cpu_cgroup(monkeypatch, tmp_path, stat_body="nr_periods 12\nnr_throttled 0\n")

    with pytest.raises(ResourceBudgetError, match="usage_usec"):
        ContainerCpuSampler().sample(_budget(), _MEASURED_HOST_PERCENT)


def test_unparseable_cpu_usage_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_cpu_cgroup(monkeypatch, tmp_path, stat_body="usage_usec 不是数字\n")

    with pytest.raises(ResourceBudgetError, match="拒绝按宿主使用率估算"):
        ContainerCpuSampler().sample(_budget(), _MEASURED_HOST_PERCENT)


def test_first_sample_reports_no_window_instead_of_the_host_percent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stepping_clock: None,
) -> None:
    """进程内首次采样没有窗口可求差，此时也绝不拿宿主使用率顶替。

    与被替换掉的 ``psutil.cpu_percent(interval=None)`` 首次调用返回 0.0 一致，
    下一次心跳即为真值。
    """
    stat = _point_cpu_cgroup(monkeypatch, tmp_path)
    assert stat is not None

    first = ContainerCpuSampler().sample(_budget(), _MEASURED_HOST_PERCENT)

    assert first.percent == 0.0
    assert first.percent != _MEASURED_HOST_PERCENT


def test_bare_metal_without_cpu_quota_reports_the_host_percent_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组（非证伪项）：没有 CPU 配额 = 裸机，整机使用率就是正确答案。

    修复前后都应该绿。它存在的意义是证明上面几条不是"永远返回 cgroup 常数"——
    真的会随来源改变，而不是把宿主分支也一起写死。
    """
    _point_cpu_cgroup(monkeypatch, tmp_path, quota=None, stat_present=False)

    usage = ContainerCpuSampler().sample(_budget(), _MEASURED_HOST_PERCENT)

    assert usage.percent == _MEASURED_HOST_PERCENT
    assert usage.source is resource_budget.BudgetSource.HOST
