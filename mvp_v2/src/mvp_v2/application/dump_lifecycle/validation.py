from __future__ import annotations

from typing import Literal

from mvp_v2.domain.invariants import validate_graph
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.domain.validation import (
    ValidationIssue,
    ValidationResult,
    build_validation_result,
)
from mvp_v2.ports.media import MediaResolver, MediaVerificationError

ValidationMode = Literal["analysis", "publication"]


def validate_snapshot(
    snapshot: GraphSnapshot,
    *,
    mode: ValidationMode,
    media_resolver: MediaResolver | None = None,
) -> ValidationResult:
    """Validate graph policy and media evidence for analysis or publication."""
    issues = list(validate_graph(snapshot).issues)
    issues.extend(_validate_media(snapshot, mode=mode, media_resolver=media_resolver))
    return build_validation_result(issues)


def _validate_media(
    snapshot: GraphSnapshot,
    *,
    mode: ValidationMode,
    media_resolver: MediaResolver | None,
) -> list[ValidationIssue]:
    if not snapshot.assets:
        return []
    if media_resolver is None:
        severity: Literal["error", "warning"] = (
            "error" if mode == "publication" else "warning"
        )
        return [
            ValidationIssue(
                severity=severity,
                code="MEDIA_RESOLVER_REQUIRED"
                if mode == "publication"
                else "NOT_VERIFIED",
                table="Asset",
                record_id=asset.id,
                message="asset media was not verified against an authority root",
            )
            for asset in snapshot.assets
        ]
    issues: list[ValidationIssue] = []
    for asset in snapshot.assets:
        try:
            verification = media_resolver.verify(asset.local_path, asset.sha256)
        except MediaVerificationError:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="MEDIA_VERIFICATION_FAILED",
                    table="Asset",
                    record_id=asset.id,
                    message="media verification adapter failed",
                )
            )
            continue
        if verification.status == "verified":
            continue
        issues.append(
            ValidationIssue(
                severity="error",
                code=f"MEDIA_{verification.status.upper()}",
                table="Asset",
                record_id=asset.id,
                message=verification.detail or f"asset media is {verification.status}",
            )
        )
    return issues
