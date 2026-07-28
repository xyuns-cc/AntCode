"""Stateful ArtifactService fake for transport contracts."""

from __future__ import annotations

from typing import Any

from antcode_contracts import artifact_pb2, artifact_pb2_grpc


class FakeArtifactService(artifact_pb2_grpc.ArtifactServiceServicer):
    def __init__(self, state: Any) -> None:
        self._state = state

    def _valid_generation(self, worker_id: str, lease_id: str) -> bool:
        expected = f"lease-{self._state.worker_id}"
        return worker_id == self._state.worker_id and lease_id == expected

    async def ClaimRunOwnership(self, request: Any, context: Any) -> Any:  # noqa: N802, ARG002
        owner = (request.worker_id, request.lease_id)
        current = self._state.run_owners.get(request.run_id)
        acquired = self._valid_generation(*owner) and current in {None, owner}
        if acquired:
            self._state.run_owners[request.run_id] = owner
        return artifact_pb2.RunOwnershipClaimResponse(acquired=acquired)

    async def RenewRunOwnership(self, request: Any, context: Any) -> Any:  # noqa: N802, ARG002
        owner = (request.worker_id, request.lease_id)
        renewed = self._valid_generation(*owner) and self._state.run_owners.get(request.run_id) == owner
        return artifact_pb2.RunOwnershipRenewResponse(renewed=renewed)

    async def ReleaseRunOwnership(self, request: Any, context: Any) -> Any:  # noqa: N802, ARG002
        owner = (request.worker_id, request.lease_id)
        released = self._state.run_owners.get(request.run_id) == owner
        if released:
            self._state.run_owners.pop(request.run_id, None)
        return artifact_pb2.RunOwnershipReleaseResponse(released=released)
