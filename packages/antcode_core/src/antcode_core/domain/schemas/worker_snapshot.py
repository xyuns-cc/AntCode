"""Worker 自报快照读不回来时的显式载荷。

``workers.metrics`` / ``workers.capabilities`` 由 Worker 二进制写、由控制面 schema
读回，两端各自发布，键集迟早会错配。错配时有三条路，前两条都不能走：

- 塌成默认值：cpu=0 / maxConcurrentTasks=5 与一台真正空闲的 Worker 逐字节相同，
  页面、日志、告警都分辨不出来（25d4c34 修掉的就是这个）；
- 让 ValidationError 冒到路由外：一台机器的一个新键会把 ``GET /workers`` 整页打成
  500，响应体只有"服务器内部错误"，运维连是哪台哪个键都看不出（真机实测）。

第三条：坏的那一列置 null，同时把"哪一列、哪几个键、什么原因"原样带进响应体。
坏行仍然出现在列表里且看得出坏，好行照常返回真值。

坏法有两种，remediation 不同，所以 ``reason`` 是个枚举而不是让调用方去 match
``message`` 里的中文（仓里有 ``"NOSCRIPT" in str(exc)`` 这种字符串契约的前科）：

- ``FIELD_MISMATCH`` 键集/取值漂移，整列还是个 JSON 对象。``keys`` 指得出是哪几个键，
  处理办法是补读回 schema 或修生产者的取值；
- ``NOT_AN_OBJECT`` 整列根本不是 JSON 对象（jsonb 存进了数组、或被二次编码成字符串）。
  这时没有"哪个键"可指，``keys`` 必然为空，能说的只有"实际存的是什么类型"，处理办法
  是去查写这一列的那条路径。两者混成一句话，运维会照着键名去补一个并不存在的 schema。
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

METRICS_COLUMN = "metrics"
CAPABILITIES_COLUMN = "capabilities"


class SnapshotErrorReason(StrEnum):
    """读回失败的类别。前端按它选文案，不许去匹配 ``message`` 的自然语言。"""

    FIELD_MISMATCH = "field_mismatch"
    NOT_AN_OBJECT = "not_an_object"


class WorkerSnapshotError(BaseModel):
    """一列 Worker 自报快照的读回失败详情。

    只在"这一列有内容但读不回来"时出现。列为空是另一回事——那台机器还没上报过，
    对应的列返回 null 且**不带**本对象，前端据此把"还没心跳"与"读取失败"画成两个东西
    （web/.../WorkerMetricCell.tsx）。
    """

    column: str = Field(..., description="读不回来的列：metrics / capabilities")
    reason: SnapshotErrorReason = Field(
        default=SnapshotErrorReason.FIELD_MISMATCH,
        description="失败类别：field_mismatch 键集/取值漂移；not_an_object 整列不是 JSON 对象",
    )
    keys: list[str] = Field(default_factory=list, description="导致失败的键名（含嵌套路径）；结构坏了时为空")
    message: str = Field("", description="逐键的失败原因")

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "CAPABILITIES_COLUMN",
    "METRICS_COLUMN",
    "SnapshotErrorReason",
    "WorkerSnapshotError",
]
