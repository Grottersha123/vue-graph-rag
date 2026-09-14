# MVP v2

Local multimodal Graph RAG with a strict, versioned graph contract.

- Canonical schema: [`docs/schema.md`](docs/schema.md)
- Implementation plan: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Package map: [`docs/module-map.md`](docs/module-map.md)

## Phase 3: legacy migration

`CsvLegacyImporter.preview(source_root)` is read-only. It reads only the 15
allowlisted legacy CSV files and regular files below `documents/`, `images/`, and
`uploads/`. It returns a canonical `GraphSnapshot`, a deterministic import report,
and a verified copy plan. It does not copy media or publish a dump.

A commit is a separate application flow through `LegacyMigrationService`:

1. preview and persist/review `result.report.canonical_bytes()`;
2. pass the exact `result.report.report_id` to `copy_media`, with an operator-chosen
   new media authority root;
3. pass the same report ID, returned resolver, an operator-chosen dumps root, and an
   explicit UTC timestamp to `publish`.

The service refuses a blocking report, approval mismatch, failed media verification,
or invalid publication graph. Approval binds the exact canonical graph rows and every
ordered copy-plan entry. Immediately before dump publication, every copied document,
image, upload, and orphan file is re-hashed through the target resolver.

Media and dumps use separate operator roots, so the two publications are not one
atomic transaction. If dump publication fails after a successful media copy, the
new immutable media authority can remain unreferenced by any dump and may be reused
for a corrected explicit publish. The filesystem repository publishes all 21 canonical
CSV tables plus `manifest.json` as an immutable `dump-NNNNNN`. It uses an exclusive
lock, same-filesystem staging, atomic rename, and file/directory `fsync`. A failed
attempt creates no numbered dump. Preview never implies commit.

Offline verification commands:

```console
/tmp/vue-graph-rag-mvp-v2/.venv/bin/ruff format --check .
/tmp/vue-graph-rag-mvp-v2/.venv/bin/ruff check .
/tmp/vue-graph-rag-mvp-v2/.venv/bin/mypy src tests
/tmp/vue-graph-rag-mvp-v2/.venv/bin/pytest -q
```

The legacy `../mvp/mvp_data` tree is always a read-only input. Never read its
`.lbdb`, `.env`, backup CSV contents, or unexpected root files.
