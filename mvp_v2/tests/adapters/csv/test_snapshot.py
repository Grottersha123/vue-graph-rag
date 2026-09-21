from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mvp_v2.adapters.csv.snapshot import CsvSnapshotError, read_snapshot, write_snapshot
from mvp_v2.adapters.ladybug import load_verified_dump
from mvp_v2.application.dto.importing import canonical_json_bytes
from mvp_v2.application.manifests import (
    DUMP_FORMAT,
    DumpFileEntry,
    DumpManifest,
    compute_dump_run_id,
)
from mvp_v2.domain.schema_registry import (
    LEGACY_GRAPH_SCHEMA_VERSION,
    TABLE_NAMES,
    TABLE_SPECS,
)
from tests.factories import build_valid_snapshot


def test_snapshot_round_trips_and_writes_deterministic_crlf(tmp_path: Path) -> None:
    snapshot = build_valid_snapshot()
    entries = write_snapshot(tmp_path / "first", snapshot)
    reordered = snapshot.model_copy(
        update={"embeddings": tuple(reversed(snapshot.embeddings))}
    )
    second_entries = write_snapshot(tmp_path / "second", reordered)

    assert read_snapshot(tmp_path / "first") == snapshot
    assert entries == second_entries
    assert tuple(entry.path for entry in entries) == tuple(
        f"tables/{name}.csv" for name in TABLE_NAMES
    )
    for entry in entries:
        contents = (tmp_path / "first" / entry.path).read_bytes()
        assert b"\r\n" in contents
        assert contents.replace(b"\r\n", b"").find(b"\n") == -1
        assert entry.sha256 == hashlib.sha256(contents).hexdigest()


def test_empty_snapshot_creates_every_table_and_manifest_fact(tmp_path: Path) -> None:
    from mvp_v2.domain.snapshot import GraphSnapshot

    entries = write_snapshot(tmp_path, GraphSnapshot())

    assert len(entries) == len(TABLE_NAMES)
    assert all(entry.row_count == 0 for entry in entries)
    assert all((tmp_path / entry.path).is_file() for entry in entries)
    assert read_snapshot(tmp_path) == GraphSnapshot()


@pytest.mark.parametrize(
    "replacement",
    [
        b"id,code,code\r\n",
        b"code,id\r\n",
        b"id,code\r\na,ok,extra\r\n",
        b"id,code\r\na\r\n",
    ],
)
def test_reader_rejects_bad_headers_and_cell_counts(
    tmp_path: Path, replacement: bytes
) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    (tmp_path / "tables/SourceType.csv").write_bytes(replacement)

    with pytest.raises(CsvSnapshotError):
        read_snapshot(tmp_path)


@pytest.mark.parametrize(
    ("table_name", "header", "row"),
    [
        (
            "Document",
            "id,title,source_name,content_format,source_url,external_id,is_active",
            "document,Title,name,markdown,,,True",
        ),
        (
            "DocumentVersion",
            "id,version,sha256,local_path,created_at,is_current,error",
            "version,01,"
            + "a" * 64
            + ",documents/a.md,2026-01-02T03:04:05.000006Z,true,",
        ),
        (
            "DocumentVersion",
            "id,version,sha256,local_path,created_at,is_current,error",
            "version,1," + "a" * 64 + ",documents/a.md,2026-01-02T03:04:05Z,true,",
        ),
        (
            "Embedding",
            "id,provider,model,model_version,purpose,dimension,vector,created_at,is_current",
            "embedding,local,embed,v1,chunk_retrieval,2,"
            '"[1, NaN]",2026-01-02T03:04:05.000006Z,true',
        ),
        (
            "Embedding",
            "id,provider,model,model_version,purpose,dimension,vector,created_at,is_current",
            'embedding,local,embed,v1,chunk_retrieval,2,"[true,2.0]",2026-01-02T03:04:05.000006Z,true',
        ),
    ],
)
def test_reader_rejects_noncanonical_scalars(
    tmp_path: Path, table_name: str, header: str, row: str
) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    (tmp_path / f"tables/{table_name}.csv").write_bytes(
        f"{header}\r\n{row}\r\n".encode()
    )

    with pytest.raises(CsvSnapshotError):
        read_snapshot(tmp_path)


def test_reader_rejects_invalid_utf8(tmp_path: Path) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    (tmp_path / "tables/Role.csv").write_bytes(b"id,name\r\nrole,\xff\r\n")

    with pytest.raises(CsvSnapshotError):
        read_snapshot(tmp_path)


def test_adapter_uses_registry_properties_instead_of_header_maps() -> None:
    import mvp_v2.adapters.csv.snapshot as adapter

    adapter_names = set(vars(adapter))
    assert "NODE_PROPERTIES" not in adapter_names
    assert "HEADERS" not in adapter_names
    assert all(spec.snapshot_field for spec in TABLE_SPECS)


def test_vector_decoder_normalizes_json_integers_to_floats() -> None:
    from mvp_v2.adapters.csv.scalars import decode_scalar
    from mvp_v2.domain.schema_registry import ScalarType

    assert decode_scalar("[1.0,2.5]", ScalarType.FLOAT_VECTOR) == (1.0, 2.5)


@pytest.mark.parametrize(
    "encoded",
    ["[1, 2.5]", "[1,2.5]", "[1.00,2.5]", "[1e0,2.5]", "[-0,2.5]", " [1.0,2.5]"],
)
def test_vector_decoder_rejects_noncanonical_json(encoded: str) -> None:
    from mvp_v2.adapters.csv.scalars import CsvScalarError, decode_scalar
    from mvp_v2.domain.schema_registry import ScalarType

    with pytest.raises(CsvScalarError):
        decode_scalar(encoded, ScalarType.FLOAT_VECTOR)


def test_multiline_csv_roundtrip_preserves_exact_text(tmp_path: Path) -> None:
    import hashlib

    snapshot = build_valid_snapshot()
    content = 'comma,"quote"\n\nnext\tλ'
    chunk = snapshot.chunks[0].model_copy(
        update={
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }
    )
    asset = snapshot.assets[0].model_copy(
        update={"description": content, "ocr_text": content}
    )
    multiline = snapshot.model_copy(update={"chunks": (chunk,), "assets": (asset,)})

    write_snapshot(tmp_path, multiline)

    assert read_snapshot(tmp_path) == multiline
    assert b'"comma,""quote""\n\nnext\t' in (tmp_path / "tables/Chunk.csv").read_bytes()


def test_writer_rejects_preexisting_table_entries(tmp_path: Path) -> None:
    tables = tmp_path / "tables"
    tables.mkdir()
    (tables / "existing").write_text("do not overwrite")

    with pytest.raises(CsvSnapshotError, match="already exists"):
        write_snapshot(tmp_path, build_valid_snapshot())


def test_reader_requires_exact_regular_table_set(tmp_path: Path) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    (tmp_path / "tables/extra.csv").write_text("")
    with pytest.raises(CsvSnapshotError, match="supported registry file set"):
        read_snapshot(tmp_path)
    (tmp_path / "tables/extra.csv").unlink()
    (tmp_path / "tables/Role.csv").unlink()
    with pytest.raises(CsvSnapshotError, match="supported registry file set"):
        read_snapshot(tmp_path)


@pytest.mark.parametrize("target_inside", [True, False])
def test_reader_rejects_table_file_symlinks(
    tmp_path: Path, target_inside: bool
) -> None:
    write_snapshot(tmp_path / "dump", build_valid_snapshot())
    role_path = tmp_path / "dump/tables/Role.csv"
    role_path.unlink()
    target = (
        tmp_path / "dump/tables/Status.csv"
        if target_inside
        else tmp_path / "outside.csv"
    )
    if not target_inside:
        target.write_text("outside")
    role_path.symlink_to(target)

    with pytest.raises(CsvSnapshotError, match="regular file"):
        read_snapshot(tmp_path / "dump")


def test_reader_and_writer_reject_directory_symlinks(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    write_snapshot(real_root, build_valid_snapshot())
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(CsvSnapshotError, match="root"):
        read_snapshot(linked_root)
    other_root = tmp_path / "other"
    other_root.mkdir()
    (other_root / "tables").symlink_to(real_root / "tables", target_is_directory=True)
    with pytest.raises(CsvSnapshotError, match="symlink"):
        write_snapshot(other_root, build_valid_snapshot())


@pytest.mark.parametrize("mutation", ["line_endings", "unneeded_quotes", "row_order"])
def test_reader_rejects_noncanonical_table_bytes(tmp_path: Path, mutation: str) -> None:
    from mvp_v2.domain import graph

    snapshot = build_valid_snapshot().model_copy(
        update={
            "roles": (
                graph.Role(id="role", name="student"),
                graph.Role(id="role-two", name="support"),
            )
        }
    )
    write_snapshot(tmp_path, snapshot)
    role_path = tmp_path / "tables/Role.csv"
    encoded = role_path.read_bytes()
    if mutation == "line_endings":
        encoded = encoded.replace(b"\r\n", b"\n")
    elif mutation == "unneeded_quotes":
        encoded = encoded.replace(b"role,student", b'"role",student')
    else:
        lines = encoded.split(b"\r\n")
        encoded = b"\r\n".join((lines[0], lines[2], lines[1], lines[3]))
    role_path.write_bytes(encoded)

    with pytest.raises(CsvSnapshotError, match="canonical"):
        read_snapshot(tmp_path)


def test_scalar_diagnostic_names_table_row_and_column(tmp_path: Path) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    document_path = tmp_path / "tables/Document.csv"
    document_path.write_bytes(
        document_path.read_bytes().replace(b",true,\r\n", b",True,\r\n")
    )

    with pytest.raises(CsvSnapshotError, match=r"Document row 2 column is_active"):
        read_snapshot(tmp_path)


def test_write_diagnostic_names_table_record_and_column(tmp_path: Path) -> None:
    snapshot = build_valid_snapshot()
    embedding = snapshot.embeddings[0].model_copy(
        update={"vector": (float("nan"), 1.0)}
    )
    broken = snapshot.model_copy(
        update={"embeddings": (embedding, *snapshot.embeddings[1:])}
    )

    with pytest.raises(
        CsvSnapshotError, match=r"Embedding row 2 record .* column vector"
    ):
        write_snapshot(tmp_path, broken)


def test_document_version_error_multiline_csv_roundtrip(tmp_path: Path) -> None:
    snapshot = build_valid_snapshot()
    error_text = 'line,"quoted"\n\nnext\tdetail λ'
    version = snapshot.document_versions[0].model_copy(update={"error": error_text})
    multiline = snapshot.model_copy(update={"document_versions": (version,)})

    write_snapshot(tmp_path, multiline)

    assert read_snapshot(tmp_path) == multiline


def test_failed_late_serialization_cleans_staging_and_allows_retry(
    tmp_path: Path,
) -> None:
    snapshot = build_valid_snapshot()
    bad_embedding = snapshot.embeddings[0].model_copy(
        update={"vector": (float("nan"), 1.0)}
    )
    broken = snapshot.model_copy(update={"embeddings": (bad_embedding,)})

    with pytest.raises(CsvSnapshotError):
        write_snapshot(tmp_path, broken)

    assert not (tmp_path / "tables").exists()
    assert not tuple(tmp_path.glob(".tables-*"))
    assert len(write_snapshot(tmp_path, snapshot)) == len(TABLE_NAMES)


def test_failed_table_io_cleans_staging_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = Path.write_bytes
    call_count = 0

    def fail_late(path: Path, encoded: bytes) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 20:
            raise OSError("injected")
        return original_write(path, encoded)

    monkeypatch.setattr(Path, "write_bytes", fail_late)
    with pytest.raises(CsvSnapshotError, match="cannot write"):
        write_snapshot(tmp_path, build_valid_snapshot())
    monkeypatch.undo()

    assert not (tmp_path / "tables").exists()
    assert not tuple(tmp_path.glob(".tables-*"))
    assert len(write_snapshot(tmp_path, build_valid_snapshot())) == len(TABLE_NAMES)


def _rewrite_legacy_table(
    path: Path, header: tuple[str, ...], rows: list[dict[str, str]]
) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream, fieldnames=header, dialect="excel", lineterminator="\r\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(stream.getvalue(), encoding="utf-8", newline="")


def test_reader_migrates_exact_v1_headers_and_missing_relation(
    tmp_path: Path,
) -> None:
    write_snapshot(tmp_path, build_valid_snapshot())
    tables = tmp_path / "tables"
    (tables / "HAS_SUMMARY_EMBEDDING.csv").unlink()

    document_rows = list(csv.DictReader((tables / "Document.csv").open(newline="")))
    for row in document_rows:
        row.pop("summary")
    _rewrite_legacy_table(
        tables / "Document.csv",
        (
            "id",
            "title",
            "source_name",
            "content_format",
            "source_url",
            "external_id",
            "is_active",
        ),
        document_rows,
    )

    chunk_rows = list(csv.DictReader((tables / "Chunk.csv").open(newline="")))
    for row in chunk_rows:
        row.pop("summary")
    _rewrite_legacy_table(
        tables / "Chunk.csv",
        ("id", "position", "content", "content_hash"),
        chunk_rows,
    )

    asset_rows = list(csv.DictReader((tables / "Asset.csv").open(newline="")))
    for row in asset_rows:
        row["caption"] = row.pop("description")
        row["llm_description"] = "ignored legacy description"
    _rewrite_legacy_table(
        tables / "Asset.csv",
        (
            "id",
            "asset_type",
            "mime_type",
            "source_url",
            "local_path",
            "external_id",
            "sha256",
            "position",
            "caption",
            "llm_description",
            "ocr_text",
        ),
        asset_rows,
    )

    embedding_rows = list(csv.DictReader((tables / "Embedding.csv").open(newline="")))
    for row in embedding_rows:
        row["purpose"] = (
            "chunk_retrieval"
            if row["id"] == "embedding-chunk"
            else "asset_description_retrieval"
        )
        row.pop("summary_vector")
    legacy_embedding_header = (
        "id",
        "provider",
        "model",
        "model_version",
        "purpose",
        "dimension",
        "vector",
        "created_at",
        "is_current",
    )
    _rewrite_legacy_table(
        tables / "Embedding.csv", legacy_embedding_header, embedding_rows
    )
    chunk_row = next(row for row in embedding_rows if row["id"] == "embedding-chunk")
    chunk_row["purpose"] = "asset_description_retrieval"
    _rewrite_legacy_table(
        tables / "Embedding.csv", legacy_embedding_header, embedding_rows
    )
    with pytest.raises(CsvSnapshotError, match="owner relation"):
        read_snapshot(tmp_path)
    chunk_row["purpose"] = "chunk_retrieval"
    _rewrite_legacy_table(
        tables / "Embedding.csv", legacy_embedding_header, embedding_rows
    )

    migrated = read_snapshot(tmp_path)

    assert migrated.documents[0].summary == ""
    assert migrated.chunks[0].summary == ""
    assert migrated.assets[0].description == "desc"
    assert all(item.summary_vector == () for item in migrated.embeddings)
    assert migrated.has_summary_embeddings == ()

    facts = tuple(
        DumpFileEntry(
            path=f"tables/{path.name}",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            row_count=max(0, len(list(csv.reader(path.open(newline="")))) - 1),
        )
        for path in sorted(tables.glob("*.csv"))
    )
    published_at = datetime(2026, 9, 21, tzinfo=UTC)
    run_id = compute_dump_run_id(
        1,
        published_at,
        facts,
        graph_schema_version=LEGACY_GRAPH_SCHEMA_VERSION,
        schema_profile_sha256="",
    )
    manifest = DumpManifest(
        format=DUMP_FORMAT,
        graph_schema_version=LEGACY_GRAPH_SCHEMA_VERSION,
        schema_profile_sha256="",
        dump_number=1,
        published_at=published_at,
        run_id=run_id,
        files=facts,
    )
    (tmp_path / "manifest.json").write_bytes(
        canonical_json_bytes(manifest.canonical_payload())
    )

    loaded_manifest, verified = load_verified_dump(tmp_path)
    assert loaded_manifest.graph_schema_version == LEGACY_GRAPH_SCHEMA_VERSION
    assert verified == migrated
