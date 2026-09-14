from __future__ import annotations

from typing import Literal, Protocol

from mvp_v2.domain.base import AllowEmptyText, ContractModel, RelativePath, Sha256


class MediaVerification(ContractModel):
    status: Literal["verified", "missing", "unreadable", "hash_mismatch"]
    detail: AllowEmptyText = ""


class MediaVerificationError(Exception):
    """An expected media verification operation failed before an outcome."""


class MediaResolver(Protocol):
    """Verify media below an implementation-owned authority root without mutation."""

    def verify(
        self, relative_path: RelativePath, expected_sha256: Sha256
    ) -> MediaVerification:
        """Return a bounded verification outcome; never copy or delete the asset."""
