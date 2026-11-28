from __future__ import annotations

import pytest

from tests.factories import build_valid_snapshot
from vue_rag.application.dump_lifecycle.validation import validate_snapshot
from vue_rag.ports.media import MediaVerification, MediaVerificationError


class FakeMediaResolver:
    def __init__(self, verification: MediaVerification) -> None:
        self._verification = verification

    def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
        assert relative_path == "images/a.png"
        assert expected_sha256 == "a" * 64
        return self._verification


def test_analysis_without_resolver_warns_explicitly() -> None:
    result = validate_snapshot(build_valid_snapshot(), mode="analysis")
    assert result.is_valid
    assert {issue.code for issue in result.warnings} == {"NOT_VERIFIED"}


def test_publication_requires_resolver() -> None:
    result = validate_snapshot(build_valid_snapshot(), mode="publication")
    assert not result.is_valid
    assert {issue.code for issue in result.errors} == {"MEDIA_RESOLVER_REQUIRED"}


@pytest.mark.parametrize("status", ["missing", "unreadable", "hash_mismatch"])
def test_media_failures_are_structured_errors(status: str) -> None:
    verification = MediaVerification.model_validate(
        {"status": status, "detail": "failed"}
    )
    result = validate_snapshot(
        build_valid_snapshot(),
        mode="publication",
        media_resolver=FakeMediaResolver(verification),
    )
    assert not result.is_valid
    assert {issue.code for issue in result.errors} == {f"MEDIA_{status.upper()}"}


def test_verified_media_allows_publication() -> None:
    resolver = FakeMediaResolver(MediaVerification(status="verified"))
    assert validate_snapshot(
        build_valid_snapshot(), mode="publication", media_resolver=resolver
    ).is_valid


class RaisingMediaResolver:
    def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
        raise MediaVerificationError("authority unavailable")


def test_empty_asset_snapshot_needs_no_media_resolver() -> None:
    snapshot = build_valid_snapshot().model_copy(
        update={
            "assets": (),
            "embeddings": tuple(
                item
                for item in build_valid_snapshot().embeddings
                if item.id != "embedding-asset"
            ),
            "has_assets": (),
            "contains_assets": (),
            "has_embedding_assets": (),
        }
    )
    assert not validate_snapshot(snapshot, mode="analysis").warnings
    assert validate_snapshot(snapshot, mode="publication").is_valid


def test_media_verification_exception_becomes_error_and_continues() -> None:
    result = validate_snapshot(
        build_valid_snapshot(),
        mode="publication",
        media_resolver=RaisingMediaResolver(),
    )
    assert {issue.code for issue in result.errors} == {"MEDIA_VERIFICATION_FAILED"}
    assert result.errors[0].record_id == "asset"


@pytest.mark.parametrize(
    "exception_text",
    ["", "line one\nline two", "tab\tdetail", "nul\x00detail", "путь/秘密"],
)
def test_media_exception_text_is_never_exposed_or_revalidated(
    exception_text: str,
) -> None:
    class MessageRaisingResolver:
        def verify(self, relative_path: str, expected_sha256: str) -> MediaVerification:
            raise MediaVerificationError(exception_text)

    result = validate_snapshot(
        build_valid_snapshot(),
        mode="publication",
        media_resolver=MessageRaisingResolver(),
    )

    assert not result.is_valid
    assert result.errors[0].code == "MEDIA_VERIFICATION_FAILED"
    assert result.errors[0].message == "media verification adapter failed"
    if exception_text:
        assert exception_text not in result.errors[0].message
