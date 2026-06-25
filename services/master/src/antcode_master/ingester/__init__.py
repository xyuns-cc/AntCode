"""数据面 loop 集合

负责把 Worker 推上来的结果/日志落库的高吞吐数据流，
按 P4 重构默认使用 *hot* Redis 连接池。

成员：
- ``result_loop``：消费 ``task:result`` Stream 落 TaskExecution
- ``log_ingest_loop``：消费 ``logs:*`` Stream 落 TaskLog
"""

from antcode_master.ingester.log_ingest_loop import log_ingest_loop
from antcode_master.ingester.result_loop import result_loop

__all__ = [
    "log_ingest_loop",
    "result_loop",
]
