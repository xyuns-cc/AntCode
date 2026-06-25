"""可观测性工具模块。

提供 W3C TraceContext 工具(``tracing``)及相关上下文管理:

- ``new_trace`` / ``child_span`` / ``parse_traceparent``: trace 生成与解析
- ``set_current_trace`` / ``get_current_trace``: contextvar 风格的当前 trace
  绑定,供 loguru 自动注入到日志 ``extra``
- ``inject_trace`` / ``extract_trace_id``: 与 ``contracts/proto`` 中
  ``TraceContext trace = 100`` 字段配合,实现 RPC 端到端透传
"""

from antcode_core.observability.tracing import (
    TraceIds,
    child_span,
    clear_current_trace,
    extract_trace_id,
    extract_traceparent,
    get_current_trace,
    get_current_trace_id,
    inject_trace,
    new_trace,
    parse_traceparent,
    set_current_trace,
)

__all__ = [
    "TraceIds",
    "child_span",
    "clear_current_trace",
    "extract_trace_id",
    "extract_traceparent",
    "get_current_trace",
    "get_current_trace_id",
    "inject_trace",
    "new_trace",
    "parse_traceparent",
    "set_current_trace",
]
