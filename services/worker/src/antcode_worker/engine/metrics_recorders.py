"""Dependency-injected Worker execution metric recorders."""

from __future__ import annotations

import os
from collections.abc import Callable

SPIDER_STATS_PATH_ENV = "ANTCODE_SPIDER_STATS_PATH"
SpiderStatsRecorder = Callable[[str], None]
TaskCompletedRecorder = Callable[[str], None]


class WorkerMetricsRecorderMixin:
    _spider_stats_recorder: SpiderStatsRecorder | None = None
    _task_completed_recorder: TaskCompletedRecorder | None = None

    def set_spider_stats_recorder(self, recorder: SpiderStatsRecorder) -> None:
        self._spider_stats_recorder = recorder

    def set_task_completed_recorder(self, recorder: TaskCompletedRecorder) -> None:
        self._task_completed_recorder = recorder

    def _record_spider_stats(self, environment: dict[str, str]) -> None:
        if self._spider_stats_recorder is None:
            return
        path = environment.get(SPIDER_STATS_PATH_ENV, "")
        if path and os.path.lexists(path):
            self._spider_stats_recorder(path)

    def _record_task_completed(self, project_id: str) -> None:
        if self._task_completed_recorder is not None:
            self._task_completed_recorder(project_id)


__all__ = ["WorkerMetricsRecorderMixin"]
