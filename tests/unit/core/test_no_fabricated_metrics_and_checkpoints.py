"""失败路径不得伪造成功数据。

覆盖 P0 回归 B13: 系统指标采集失败时,不允许返回"全零"的 SystemMetrics 冒充健康机器。

同批次的 B14(检查点)用例已随 core 侧死副本删除而迁移到
``tests/unit/master/test_master_checkpoint_integrity.py``,那里测的是生产
实际在跑的 ``antcode_master.task_persistence``。
"""

from importlib import import_module
from unittest.mock import AsyncMock

import pytest

# monitoring 包把同名的服务单例挂在了包属性上，只能用 import_module 拿到模块本身
sms = import_module("antcode_core.application.services.monitoring.system_metrics_service")

_STUB_CPU_CORES = 8
_STUB_CPU_PERCENT = 12.5


class _FakeCache:
    """记录读写的缓存替身,用来断言失败时没有落下伪造数据。"""

    def __init__(self, stored=None):
        self.stored = stored
        self.writes = []
        self.deletes = []

    async def get(self, key):
        return self.stored

    async def set(self, key, value, ttl=None):
        self.writes.append((key, value))
        return True

    async def delete(self, key):
        self.deletes.append(key)
        return True

    async def get_stats(self):
        return {"name": "metrics"}


def _metrics_service(monkeypatch):
    """真实的 SystemMetricsService,仅把依赖 DB 的采集项替换为固定值。"""
    service = sms.SystemMetricsService()
    monkeypatch.setattr(service, "_collect_active_tasks", AsyncMock(return_value=1))
    monkeypatch.setattr(service, "_collect_queue_size", AsyncMock(return_value=2))
    monkeypatch.setattr(service, "_collect_success_rate", AsyncMock(return_value=50.0))
    monkeypatch.setattr(sms.psutil, "cpu_percent", lambda interval=None: _STUB_CPU_PERCENT)
    monkeypatch.setattr(sms.psutil, "cpu_count", lambda logical=True: _STUB_CPU_CORES)
    monkeypatch.setattr(sms.psutil, "boot_time", lambda: 1.0)
    return service


# ---------------------------------------------------------------- B13 指标


@pytest.mark.asyncio
async def test_psutil_failure_raises_instead_of_returning_zero_metrics(monkeypatch):
    service = _metrics_service(monkeypatch)

    def _explode():
        raise OSError("psutil 不可用")

    monkeypatch.setattr(sms.psutil, "virtual_memory", _explode)

    with pytest.raises(sms.MetricsCollectionError):
        await service._collect_metrics()


@pytest.mark.asyncio
async def test_missing_cpu_core_count_raises_instead_of_zero_cores(monkeypatch):
    """cpu_cores=0 会让下游"每核占用"换算除零,必须当成采集失败。"""
    service = _metrics_service(monkeypatch)
    monkeypatch.setattr(sms.psutil, "cpu_count", lambda logical=True: None)

    with pytest.raises(sms.MetricsCollectionError):
        await service._collect_cpu_metrics()


@pytest.mark.asyncio
async def test_non_finite_percent_raises_instead_of_defaulting_to_zero(monkeypatch):
    service = _metrics_service(monkeypatch)
    monkeypatch.setattr(sms.psutil, "cpu_percent", lambda interval=None: float("nan"))

    with pytest.raises(sms.MetricsCollectionError):
        await service._collect_cpu_metrics()


@pytest.mark.asyncio
async def test_collection_failure_propagates_and_never_caches_zero_metrics(monkeypatch):
    service = _metrics_service(monkeypatch)
    cache = _FakeCache()
    monkeypatch.setattr(sms, "metrics_cache", cache)

    def _explode(path):
        raise OSError("磁盘不可读")

    monkeypatch.setattr(sms.psutil, "disk_usage", _explode)

    with pytest.raises(sms.MetricsCollectionError):
        await service.get_metrics(force_refresh=True)

    # 关键断言:失败时缓存里不能留下任何伪造指标,否则看板会长期显示"全零健康"
    assert cache.writes == []


@pytest.mark.asyncio
async def test_successful_collection_still_returns_real_metrics(monkeypatch):
    service = _metrics_service(monkeypatch)
    monkeypatch.setattr(sms, "metrics_cache", _FakeCache())

    response = await service.get_metrics(force_refresh=True)

    assert response.cpu_cores == _STUB_CPU_CORES
    assert response.cpu_percent == _STUB_CPU_PERCENT
    assert response.memory_total > 0


@pytest.mark.asyncio
async def test_cache_info_failure_raises_instead_of_reporting_defaults(monkeypatch):
    service = sms.SystemMetricsService()
    cache = _FakeCache()

    async def _explode():
        raise OSError("redis 不可达")

    cache.get_stats = _explode
    monkeypatch.setattr(sms, "metrics_cache", cache)

    with pytest.raises(OSError):
        await service.get_cache_info()
