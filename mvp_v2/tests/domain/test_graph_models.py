from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from mvp_v2.domain.base import stable_id
from mvp_v2.domain.graph import (
    Asset,
    Chunk,
    Document,
    DocumentVersion,
    Embedding,
    HasSourceType,
    Process,
    Role,
    SourceType,
    Status,
)
from mvp_v2.domain.snapshot import GraphSnapshot

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
SHA = "a" * 64


def make_document() -> Document:
    return Document(
        id="document:one",
        title=" Title ",
        source_name="source",
        content_format="markdown",
        source_url="",
        external_id="",
        is_active=True,
    )


def test_model_is_strict_frozen_extra_forbidden_and_preserves_whitespace() -> None:
    document = make_document()
    assert document.title == " Title "
    with pytest.raises(ValidationError):
        Document(**{**document.model_dump(), "is_active": 1})
    with pytest.raises(ValidationError):
        Document(**{**document.model_dump(), "unexpected": "value"})
    with pytest.raises(ValidationError):
        document.title = "changed"


def test_entity_id_rejects_blank_and_control_characters() -> None:
    for invalid_id in ("", "   ", "line\nfeed"):
        with pytest.raises(ValidationError):
            Document(**{**make_document().model_dump(), "id": invalid_id})


def test_exact_embedding_fields_and_valid_vector() -> None:
    assert tuple(Embedding.model_fields) == (
        "custom_properties",
        "id",
        "provider",
        "model",
        "model_version",
        "dimension",
        "vector",
        "created_at",
        "is_current",
        "summary_vector",
    )
    embedding = Embedding(
        id="embedding:one",
        provider="local",
        model="text-model",
        model_version="1",
        dimension=2,
        vector=(1.0, -2.0),
        created_at=NOW,
        is_current=True,
    )
    assert embedding.model_dump(mode="json")["created_at"].endswith("Z")


@pytest.mark.parametrize("vector", [(1.0,), (float("nan"), 1.0), (True, 1.0)])
def test_embedding_rejects_bad_vectors(vector: tuple[float, ...]) -> None:
    with pytest.raises(ValidationError):
        Embedding(
            id="embedding:one",
            provider="local",
            model="text-model",
            model_version="1",
            dimension=2,
            vector=vector,
            created_at=NOW,
            is_current=True,
        )


def test_datetime_requires_exact_utc_and_python_datetime() -> None:
    base = {
        "id": "embedding:one",
        "provider": "local",
        "model": "text-model",
        "model_version": "1",
        "dimension": 1,
        "vector": (1.0,),
        "is_current": True,
    }
    for timestamp in (
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        "2026-01-01T00:00:00Z",
    ):
        with pytest.raises(ValidationError):
            Embedding.model_validate({**base, "created_at": timestamp})


def test_chunk_hash_is_derived_from_exact_content() -> None:
    content = " text "
    chunk = Chunk(
        id="chunk:one",
        position=0,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    assert chunk.content == content
    with pytest.raises(ValidationError):
        Chunk(id="chunk:one", position=0, content=content, content_hash=SHA)


def test_version_sha_rules_and_relative_paths() -> None:
    failed = DocumentVersion(
        id="version:one",
        version=1,
        sha256="",
        local_path="",
        created_at=NOW,
        is_current=False,
        error="download failed",
    )
    assert failed.sha256 == ""
    with pytest.raises(ValidationError):
        DocumentVersion(**{**failed.model_dump(), "is_current": True})
    with pytest.raises(ValidationError):
        DocumentVersion(**{**failed.model_dump(), "error": ""})
    with pytest.raises(ValidationError):
        DocumentVersion(**{**failed.model_dump(), "sha256": "A" * 64})
    with pytest.raises(ValidationError):
        DocumentVersion(**{**failed.model_dump(), "local_path": "../secret"})


def test_asset_type_hash_position_and_path() -> None:
    values = {
        "id": "asset:one",
        "asset_type": "image",
        "mime_type": "image/png",
        "source_url": "",
        "local_path": "images/a.png",
        "external_id": "",
        "sha256": SHA,
        "position": 0,
        "description": "",
        "ocr_text": "",
    }
    assert Asset.model_validate(values).asset_type == "image"
    for updates in (
        {"asset_type": "video"},
        {"sha256": "A" * 64},
        {"position": -1},
        {"local_path": ""},
        {"local_path": "/tmp/a.png"},
        {"local_path": "images\\a.png"},
    ):
        with pytest.raises(ValidationError):
            Asset.model_validate({**values, **updates})


def test_relation_accepts_keywords_and_csv_aliases() -> None:
    relation = HasSourceType(source_id="document:one", target_id="source:web")
    assert relation.model_dump(by_alias=True) == {
        "from": "document:one",
        "to": "source:web",
    }
    assert (
        HasSourceType.model_validate({"from": "document:one", "to": "source:web"})
        == relation
    )
    with pytest.raises(ValidationError):
        HasSourceType.model_validate(
            {"from": "document:one", "to": "source:web", "other": "bad"}
        )


def test_snapshot_contains_typed_immutable_tuples() -> None:
    document = make_document()
    snapshot = GraphSnapshot(documents=(document,))
    assert snapshot.documents == (document,)
    with pytest.raises(ValidationError):
        GraphSnapshot.model_validate({"documents": [document]})
    with pytest.raises(ValidationError):
        snapshot.documents = ()


def legacy_stable_id(prefix: str, *parts: str) -> str:
    if not prefix or any(not isinstance(part, str) for part in parts):
        raise ValueError("stable IDs require a kind and string parts")
    payload = "\x1f".join((prefix, *parts)).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:24]
    return f"{prefix}-{digest}"


def test_stable_id_has_exact_legacy_parity_and_golden_value() -> None:
    assert stable_id("chunk", "doc", "0") == "chunk-22ce4e0497f1f8a9f139a23f"
    cases = (
        ("chunk", ()),
        (" ", ("part",)),
        ("bad\nkind", ("part",)),
        ("chunk", ("",)),
        ("chunk", ("bad\x1fpart",)),
        ("chunk", ("bad\u0085part", " tail ")),
    )
    for prefix, parts in cases:
        assert stable_id(prefix, *parts) == legacy_stable_id(prefix, *parts)


def test_stable_id_preserves_legacy_exception_behavior() -> None:
    for prefix in ("", None, 0):
        with pytest.raises(
            ValueError, match="stable IDs require a kind and string parts"
        ):
            stable_id(prefix, "part")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="stable IDs require a kind and string parts"):
        stable_id("chunk", 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        stable_id(1, "part")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_text", ["", "   ", "bad\ntext", "bad\u0085text", "bad\u200btext"]
)
def test_required_semantic_text_rejects_blank_or_unicode_controls(
    bad_text: str,
) -> None:
    document = make_document().model_dump()
    for field in ("title", "content_format"):
        with pytest.raises(ValidationError):
            Document.model_validate({**document, field: bad_text})


def test_optional_text_allows_empty_but_rejects_controls_without_normalizing() -> None:
    document = make_document()
    assert (
        Document.model_validate(
            {**document.model_dump(), "source_name": ""}
        ).source_name
        == ""
    )
    assert (
        Document.model_validate(
            {**document.model_dump(), "source_name": " value "}
        ).source_name
        == " value "
    )
    with pytest.raises(ValidationError):
        Document.model_validate(
            {**document.model_dump(), "source_name": "bad\u0085value"}
        )


@pytest.mark.parametrize(
    ("model", "values", "field"),
    [
        (SourceType, {"id": "source:one", "code": "local"}, "code"),
        (
            Chunk,
            {
                "id": "chunk:one",
                "position": 0,
                "content": "x",
                "content_hash": hashlib.sha256(b"x").hexdigest(),
            },
            "content",
        ),
        (Role, {"id": "role:one", "name": "student"}, "name"),
        (
            Process,
            {
                "id": "process:one",
                "name": "onboarding",
                "status": "active",
                "owner": "",
            },
            "name",
        ),
        (
            Process,
            {
                "id": "process:one",
                "name": "onboarding",
                "status": "active",
                "owner": "",
            },
            "status",
        ),
        (Status, {"id": "status:one", "name": "ready", "description": ""}, "name"),
        (
            Embedding,
            {
                "id": "embedding:two",
                "provider": "local",
                "model": "embed",
                "model_version": "v1",
                "dimension": 1,
                "vector": (1.0,),
                "created_at": NOW,
                "is_current": True,
            },
            "provider",
        ),
        (
            Asset,
            {
                "id": "asset:two",
                "asset_type": "image",
                "mime_type": "image/png",
                "source_url": "",
                "local_path": "images/two.png",
                "external_id": "",
                "sha256": SHA,
                "position": 0,
                "description": "",
                "ocr_text": "",
            },
            "mime_type",
        ),
    ],
)
def test_all_required_semantic_fields_reject_blank_and_control(
    model: type[BaseModel], values: dict[str, object], field: str
) -> None:
    for invalid in ("", "  ", "bad\u0085value"):
        with pytest.raises(ValidationError):
            model.model_validate({**values, field: invalid})


def test_multiline_fields_preserve_lf_tabs_quotes_and_unicode() -> None:
    content = 'first,"quoted"\n\nsecond\tλ'
    chunk = Chunk(
        id="chunk:multiline",
        position=0,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    asset = Asset(
        id="asset:multiline",
        asset_type="image",
        mime_type="image/png",
        source_url="",
        local_path="images/multiline.png",
        external_id="",
        sha256=SHA,
        position=0,
        description=content,
        ocr_text=content,
    )
    assert chunk.content == content
    assert asset.description == content


@pytest.mark.parametrize(
    "value", ["line\rbreak", "nul\x00", "format\u200b", "line\u2028separator"]
)
def test_multiline_fields_reject_noncanonical_controls(value: str) -> None:
    with pytest.raises(ValidationError):
        Chunk(
            id="chunk:bad-control",
            position=0,
            content=value,
            content_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        )


@pytest.mark.parametrize("value", ["", " ", "\n\t"])
def test_required_multiline_rejects_blank_text(value: str) -> None:
    with pytest.raises(ValidationError):
        Chunk(
            id="chunk:blank",
            position=0,
            content=value,
            content_hash=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        )
