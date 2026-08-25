"""Worker HMAC 认证的拒绝原因：结构化码 + 各自对应的运维动作。

``verify_signature_async`` 以前把五种互不相干的拒绝——身份不存在、时间戳超窗、
签名不符、nonce 重放、签名版本不支持——全折叠成一个 bool，边界层再统一回
``401 签名验证失败``。这条文案离"控制面库里根本没有这个 worker_id"极远：控制面
库一旦重建或回滚到注册之前，全部存量 Worker 的本地凭据仍然**结构上合法**，却在
第一次签名请求上被拒，运维照字面去查 HMAC 密钥和时钟，而真因是身份不存在、
唯一出路是清本地凭据重新注册。判据回答的是"这份凭据长得对不对"，调用方要问的
却是"这份凭据还认不认"。

码经 ``http_exception_handler`` 透出到响应体 ``data.error_code``，调用方只读码、
不读文案：仓里有拿中文/英文错误串做判定的 P0 前科（``"NOSCRIPT" in str(exc)``
恒为假，一次 Redis 重启让全集群 Worker 永久掉线）。

本模块刻意不 import fastapi —— Worker 侧只需要码常量来翻译拒绝，不该为此把 HTTP
框架拖进它的启动路径；HTTPException 的构造留在 ``worker_auth`` 这个边界层模块。
"""

from __future__ import annotations

from enum import StrEnum


class WorkerAuthReason(StrEnum):
    """签名校验结论。

    ``OK`` 是一个显式取值而不是"返回真"：真假值表达不了五选一，而"用真假值回答
    一个五选一的问题"正是本模块要消灭的那个 bug。
    """

    OK = "OK"
    #: 控制面库里没有这个 worker_id，或它的 HMAC 密钥材料已被清除。
    #: 控制面库重建 / 回滚到注册之前 / Worker 已被删除，都落在这一类。
    IDENTITY_UNKNOWN = "WORKER_AUTH_IDENTITY_UNKNOWN"
    TIMESTAMP_SKEW = "WORKER_AUTH_TIMESTAMP_SKEW"
    SIGNATURE_INVALID = "WORKER_AUTH_SIGNATURE_INVALID"
    NONCE_REPLAY = "WORKER_AUTH_NONCE_REPLAY"
    SIGNATURE_VERSION_UNSUPPORTED = "WORKER_AUTH_SIGNATURE_VERSION_UNSUPPORTED"


_REJECTION_DETAILS: dict[WorkerAuthReason, str] = {
    WorkerAuthReason.IDENTITY_UNKNOWN: (
        "控制面不认识该 Worker 身份：库中没有这条记录，或其 HMAC 密钥材料已被清除。"
        "常见于控制面库被重建或回滚到注册之前——此时 Worker 本地凭据结构上仍然合法，"
        "但已永久失效，必须清除本地凭据并用新的安装 Key 重新注册。"
    ),
    WorkerAuthReason.TIMESTAMP_SKEW: "请求时间戳超出允许窗口，请校对 Worker 与控制面的时钟",
    WorkerAuthReason.SIGNATURE_INVALID: "HMAC 签名与请求内容不符",
    WorkerAuthReason.NONCE_REPLAY: "请求 nonce 已被使用，拒绝重放",
    WorkerAuthReason.SIGNATURE_VERSION_UNSUPPORTED: "不支持的签名版本",
}


def rejection_detail(reason: WorkerAuthReason) -> str:
    """取拒绝原因对应的中文说明；``OK`` 不是拒绝，直接下标让它 KeyError。

    不给默认文案：漏配一个原因必须在第一次触发时炸掉，而不是悄悄退回一句
    通用错误——那正好复制了本模块要修的"所有拒绝长得一样"。
    """
    return _REJECTION_DETAILS[reason]


__all__ = ["WorkerAuthReason", "rejection_detail"]
