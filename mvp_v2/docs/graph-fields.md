# Optional graph fields

`src/vue_rag/graph_fields.json` declares optional node properties outside the
typed core schema. The application loads this file when the schema registry is
imported, so restart long-lived processes and notebooks after changing it.

## Current declarations

The current configuration extends `Document` with three curation fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `knowledge_kind` | `STRING` | `""` | Knowledge classification. Production values are `official_documentation` and `curated_faq`. |
| `review_status` | `STRING` | `""` | Review state. Production retrieval requires `approved` when curation metadata is present. |
| `reusable_as_policy` | `BOOLEAN` | `false` | Whether approved content may be reused in a generated answer. |

Documents with curation metadata are eligible for production retrieval only when:

```text
knowledge_kind in {official_documentation, curated_faq}
review_status == approved
reusable_as_policy == true
```

Legacy records that reach retrieval without any of the three properties remain
searchable. Current-schema CSV loading injects configured defaults, so documents in
current dumps must populate the complete approved policy contract. An empty or
partially populated contract does not pass the production gate.

Retrieval derives `Evidence.source_kind` from these values:

- `curated_faq` becomes `faq`;
- `official_documentation` becomes `documentation`;
- other values become `other`.

An `external_id` beginning with `faq:` is retained as a compatibility fallback for
FAQ classification. New ingestion should set `knowledge_kind` explicitly.

## File contract

The file has version `1` and this shape:

```json
{
  "version": 1,
  "nodes": {
    "Document": [
      {"name": "knowledge_kind", "type": "STRING", "default": ""}
    ]
  }
}
```

Supported types are `STRING`, `INT64`, `BOOLEAN`, and `FLOAT_VECTOR`. Every field
requires a default of the same type. Field names use lowercase snake case. A node
cannot declare duplicate fields or replace a core property.

Configured fields become ordinary CSV and LadybugDB columns. The typed domain keeps
their values in `custom_properties`; callers should not depend on that internal
sidecar representation.

## Compatibility rules

- The CSV writer always emits configured columns.
- The reader injects defaults when an older otherwise-compatible CSV lacks a
  configured column.
- Unknown, duplicate, reordered, or undeclared columns remain errors.
- Removing or changing a field can invalidate published dumps and databases.
- Prefer additive fields and rebuild derived database artifacts after a schema
  profile change.

After changing the file, validate the schema in a fresh interpreter and exercise the
real dump/database construction path. Static JSON validation alone is not enough.
