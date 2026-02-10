"""
结果消费循环

从 Redis Streams 消费 Worker 上报的执行结果并更新 TaskRun。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from antcode_core.application.services.task_run_service import task_run_service
from antcode_core.infrastructure.redis import task_result_stream
from antcode_core.infrastructure.redis.streams import StreamClient
from antcode_master.leader import ensure_leader


class ResultLoop:
    """结果消费循环"""

    def __init__(
        self,
        stream_key: str | None = None,
        group_name: str = "antcode-results",
        consumer_name: str | None = None,
        poll_interval: float = 1.0,
        block_ms: int = 5000,
        batch_size: int = 50,
        pending_check_interval: int = 30,
    ):
        self._stream_key = stream_key or task_result_stream()
        self._group = group_name
        self._consumer = consumer_name or f"{socket.gethostname()}-{id(self)}"
        self._poll_interval = poll_interval
        self._block_ms = block_ms
        self._batch_size = batch_size
        self._pending_check_interval = pending_check_interval
        self._last_pending_check = 0.0
        self._stream = StreamClient()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动结果循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "结果消费循环已启动: stream={}, group={}, consumer={}",
            self._stream_key,
            self._group,
            self._consumer,
        )

    async def stop(self) -> None:
        """停止结果循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("结果消费循环已停止")

    async def _run_loop(self) -> None:
        """主循环"""
        while self._running:
            try:
                if not await ensure_leader():
                    await asyncio.sleep(self._poll_interval)
                    continue

                messages = await self._stream.xreadgroup(
                    stream_key=self._stream_key,
                    group_name=self._group,
                    consumer_name=self._consumer,
                    count=self._batch_size,
                    block_ms=self._block_ms,
                )

                if not messages:
                    now = time.time()
                    if now - self._last_pending_check >= self._pending_check_interval:
                        self._last_pending_check = now
                        messages = await self._stream.xreadgroup(
                            stream_key=self._stream_key,
                            group_name=self._group,
                            consumer_name=self._consumer,
                            count=self._batch_size,
                            block_ms=1,
                            read_pending=True,
                        )

                    if not messages:
                        await asyncio.sleep(self._poll_interval)
                        continue

                ack_ids: list[str] = []
                for message in messages:
                    try:
                        handled = await self._handle_message(message.data)
                        if handled:
                            ack_ids.append(message.msg_id)
                    except Exception as exc:
                        logger.error(f"处理结果消息失败: {exc}")

                if ack_ids:
                    await self._stream.xack(self._stream_key, ack_ids, self._group)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"结果消费循环异常: {e}")
                await asyncio.sleep(self._poll_interval)

    async def _handle_message(self, data: dict[str, Any]) -> bool:
        """处理单条结果消息"""
        payload = self._normalize_payload(data)
        run_id = payload.get("run_id") or ""
        if not run_id:
            return True

        status = (payload.get("status") or "").lower()
        exit_code = self._to_int(payload.get("exit_code"))
        error_message = payload.get("error_message") or ""
        started_at = self._parse_dt(payload.get("started_at"))
        finished_at = self._parse_dt(payload.get("finished_at"))
        duration_ms = payload.get("duration_ms")
        result_data = payload.get("data") or {}

        return await task_run_service.update_result(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            data=result_data if isinstance(result_data, dict) else {},
        )

    def _normalize_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            normalized[key] = self._decode_value(value)
        if isinstance(normalized.get("data"), str):
            normalized["data"] = self._maybe_json(normalized["data"])
        return normalized

    def _decode_value(self, value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def _maybe_json(self, value: str) -> Any:
        try:
            return json.loads(value)
        except Exception:
            return value

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except Exception:
            return None

    def _to_int(self, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except Exception:
            return None


result_loop = ResultLoop()

__all__ = ["ResultLoop", "result_loop"]
