"""告警限流器。

从 alert_manager 拆出：「同一告警短时间内发几条」和「告警发到哪些渠道、
为什么没发出去」是两件事，且 alert_manager.py 已顶到 300 行硬上限。
"""

import time
from collections import defaultdict

from antcode_core.common.hash_utils import calculate_content_hash


class RateLimiter:
    # 超过该键数时清理已全部过期的键，防止长期运行内存无界增长
    _PRUNE_THRESHOLD = 512

    def __init__(self, window=60, max_count=3):
        self.window = window
        self.max_count = max_count
        self._records = defaultdict(list)

    def _get_message_key(self, message, level, rate_key=None):
        # rate_key 允许调用方提供不含时间戳/瞬时数值的稳定去重键，
        # 否则同一告警每次因时间戳不同而哈希不同，限流永远不生效。
        content = rate_key if rate_key else f"{level}:{message}"
        return calculate_content_hash(content)

    def _prune_expired(self, current_time):
        if len(self._records) <= self._PRUNE_THRESHOLD:
            return
        expired = [
            key
            for key, timestamps in self._records.items()
            if not timestamps or current_time - timestamps[-1] >= self.window
        ]
        for key in expired:
            del self._records[key]

    def should_allow(self, message, level, rate_key=None):
        key = self._get_message_key(message, level, rate_key)
        current_time = time.time()

        self._prune_expired(current_time)
        self._records[key] = [ts for ts in self._records[key] if current_time - ts < self.window]

        if len(self._records[key]) >= self.max_count:
            remaining = int(self.window - (current_time - self._records[key][0]))
            return False, f"限流 ({remaining}s后可用)"

        self._records[key].append(current_time)
        return True, None

    def clear(self):
        self._records.clear()

    def get_stats(self):
        current_time = time.time()
        active_keys = 0
        total_records = 0

        for _key, timestamps in self._records.items():
            valid_timestamps = [ts for ts in timestamps if current_time - ts < self.window]
            if valid_timestamps:
                active_keys += 1
                total_records += len(valid_timestamps)

        return {"active_keys": active_keys, "total_records": total_records}


__all__ = ["RateLimiter"]
