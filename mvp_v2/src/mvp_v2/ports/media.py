from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from mvp_v2.domain.base import AllowEmptyText, ContractModel, RelativePath, Sha256


class MediaVerification(ContractModel):
    status: Literal["verified", "missing", "unreadable", "hash_mismatch"]
    detail: AllowEmptyText = ""


class MediaVerificationError(Exception):
    """An expected media verification operation failed before an outcome."""


class MediaResolver(Protocol):
    """Verify media below an configured runtime data root without mutation."""

    def verify(
        self, relative_path: RelativePath, expected_sha256: Sha256
    ) -> MediaVerification:
        """Return a bounded verification outcome; never copy or delete the asset."""


class MediaDeletionError(Exception):
    """Physical media could not be safely validated or deleted."""


@dataclass(frozen=True, slots=True)
class MediaDeletionTarget:
    relative_path: RelativePath
    expected_sha256: Sha256


@dataclass(frozen=True, slots=True)
class MediaDeletionReport:
    deleted_paths: tuple[RelativePath, ...]
    missing_paths: tuple[RelativePath, ...]
    failed_paths: tuple[RelativePath, ...]


class MediaDeleter(Protocol):
    """Delete explicitly confirmed media below a configured runtime root."""

    def validate(self, targets: tuple[MediaDeletionTarget, ...]) -> None: ...

    def delete(
        self, targets: tuple[MediaDeletionTarget, ...]
    ) -> MediaDeletionReport: ...
