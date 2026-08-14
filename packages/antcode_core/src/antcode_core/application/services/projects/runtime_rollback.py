"""Compensation for Worker runtimes created inside database workflows."""

from __future__ import annotations

from dataclasses import dataclass

from antcode_core.application.services.runtime.runtime_control_service import RuntimeControlService


@dataclass(frozen=True)
class RuntimeReservation:
    worker_id: str
    env_name: str


class RuntimeRollback:
    """Tracks newly created external environments until the database commits."""

    def __init__(self, runtime_service: RuntimeControlService) -> None:
        self._runtime_service = runtime_service
        self._reservations: list[RuntimeReservation] = []

    def register(self, reservation: RuntimeReservation) -> None:
        self._reservations.append(reservation)

    async def compensate(self, primary: BaseException) -> None:
        failures: list[BaseException] = []
        for reservation in reversed(self._reservations):
            try:
                await self._delete_runtime(reservation)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise BaseExceptionGroup(
                "数据库操作失败且运行时回滚未完成",
                [primary, *failures],
            ) from primary

    async def _delete_runtime(self, reservation: RuntimeReservation) -> None:
        result = await self._runtime_service.delete_env(
            reservation.worker_id,
            reservation.env_name,
        )
        if result.get("success"):
            return
        error = str(result.get("error") or "未知错误")
        raise RuntimeError(f"删除运行时 {reservation.env_name} 失败: {error}")


__all__ = ["RuntimeReservation", "RuntimeRollback"]
