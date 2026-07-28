"""Platform facade for fail-closed artifact filesystem access."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from antcode_worker.executor.artifact_io_posix import (
        ArtifactFileIdentity,
        ArtifactSecurityError,
        VerifiedArtifactFile,
        VerifiedArtifactRoot,
        hash_regular_file,
        inspect_candidate,
        inspect_work_root,
        read_verified_regular_file,
        validate_artifact_pattern,
    )
elif os.name == "nt":
    from antcode_worker.executor.artifact_io_posix import validate_artifact_pattern
    from antcode_worker.executor.artifact_io_windows import (
        ArtifactFileIdentity,
        VerifiedArtifactFile,
        VerifiedArtifactRoot,
        hash_regular_file,
        inspect_candidate,
        inspect_work_root,
        read_verified_regular_file,
    )
    from antcode_worker.executor.artifact_io_windows import (
        WindowsArtifactSecurityError as ArtifactSecurityError,
    )
else:
    from antcode_worker.executor.artifact_io_posix import (
        ArtifactFileIdentity,
        ArtifactSecurityError,
        VerifiedArtifactFile,
        VerifiedArtifactRoot,
        hash_regular_file,
        inspect_candidate,
        inspect_work_root,
        read_verified_regular_file,
        validate_artifact_pattern,
    )

__all__ = [
    "ArtifactFileIdentity",
    "ArtifactSecurityError",
    "VerifiedArtifactFile",
    "VerifiedArtifactRoot",
    "hash_regular_file",
    "inspect_candidate",
    "inspect_work_root",
    "read_verified_regular_file",
    "validate_artifact_pattern",
]
