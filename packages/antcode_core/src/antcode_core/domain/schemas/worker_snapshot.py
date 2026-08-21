"""Worker 自报快照读不回来时的显式载荷。

``workers.metrics`` / ``workers.capabilities`` 由 Worker 二进制写、由控制面 schema
读回，两端各自发布，键集迟早会错配。错配时有三条路，前两条都不能走：

- 塌成默认值：cpu=0 / maxConcurrentTasks=5 与一台真正空闲的 Worker 逐字节相同，
  页面、日志、告警都分辨不出来（25d4c34 修掉的就是这个）；
- 让 ValidationError 冒到路由外：一台机器的一个新键会把 ``GET /workers`` 整页打成
  500，响应体只有"服务器内部错误"，运维连是哪台哪个键都看不出（真机实测）。

第三条：坏的那一列置 null，同时把"哪一列、哪几个键、什么原因"原样带进响应体。
坏行仍然出现在列表里且看得出坏，好行照常返回真值。
"""

from pydantic import BaseModel, ConfigDict, Field

METRICS_COLUMN = "metrics"
CAPABILITIES_COLUMN = "capabilities"


class WorkerSnapshotError(BaseModel):
    """一列 Worker 自报快照的读回失败详情。"""

    column: str = Field(..., description="读不回来的列：metrics / capabilities")
    keys: list[str] = Field(default_factory=list, description="导致失败的键名（含嵌套路径）")
    message: str = Field("", description="逐键的失败原因")

    model_config = ConfigDict(extra="forbid")


__all__ = ["CAPABILITIES_COLUMN", "METRICS_COLUMN", "WorkerSnapshotError"]
