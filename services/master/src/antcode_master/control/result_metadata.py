"""TaskRun 结果元数据合并。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

# P1-LOG (round6 5.3): result_data 单 run 总字节上限。
# 审查:`result_data` 只有 1 MiB 单帧限制,同一 run 可通过不同 key 无界扩大
# Redis/JSONB/WAL。1 MiB 单帧 + 无总量限制,恶意/失控 Worker 可持续 merge
# 新 key 到几百 MiB。这里加总字节 hard cap = 2 MiB,超过则丢弃新增字段并
# warn(不 crash,保留原有元数据不破坏语义)。
_MAX_RESULT_DATA_BYTES = 2 * 1024 * 1024


def merge_result_data(
    current: dict[str, Any] | None,
    update: dict[str, Any] | None,
) -> dict[str, Any]:
    """保留创建期/重试元数据，并用本阶段结果更新同名字段。

    P1-LOG (round6 5.3): 合并后总 JSON 字节超过 _MAX_RESULT_DATA_BYTES 时,
    丢弃本次 update 并 warn。之前 dict.update 无上限,同一 run 可通过不同 key
    (每次 merge 新加不同 key)无界扩大 result_data,冲击 Redis/PG JSONB/WAL。
    保留原有 current 不破坏语义,避免任务重试丢原始进度。
    """
    merged = dict(current or {})
    if not update:
        return merged
    candidate = {**merged, **update}
    # json.dumps 是 result_data 落 PG 的实际形态,用它算字节精确
    try:
        candidate_size = len(json.dumps(candidate, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        # 无法序列化就走保守路径,拒绝新 update,不破坏 current
        logger.warning("merge_result_data: update 无法 JSON 序列化,丢弃本次合并")
        return merged
    if candidate_size > _MAX_RESULT_DATA_BYTES:
        logger.warning(
            "merge_result_data: 合并后 result_data ({} bytes) 超上限 {} bytes,"
            " 丢弃本次 update({} keys) 保留 current,防 Redis/PG WAL 膨胀",
            candidate_size,
            _MAX_RESULT_DATA_BYTES,
            len(update),
        )
        return merged
    return candidate


__all__ = ["merge_result_data"]
