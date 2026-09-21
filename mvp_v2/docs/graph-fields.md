# Optional graph fields

`src/mvp_v2/graph_fields.json` declares optional node properties that are not part
of the typed core schema. Restart the application after changing this file.

Example:

```json
{
  "version": 1,
  "nodes": {
    "Document": [
      {"name": "review_note", "type": "STRING", "default": ""},
      {"name": "priority", "type": "INT64", "default": 0}
    ],
    "Embedding": [
      {"name": "aux_vector", "type": "FLOAT_VECTOR", "default": []}
    ]
  }
}
```

Supported types are `STRING`, `INT64`, `BOOLEAN`, and `FLOAT_VECTOR`. Every field
must have a default of the same type. `FLOAT_VECTOR` extensions are limited to nodes
with a `dimension` property, currently `Embedding`.

The CSV writer always emits configured columns. When an older compatible CSV lacks
a configured column, the reader injects its default. Unknown, duplicate, reordered,
or undeclared columns remain errors. Configured values are flattened into ordinary
CSV and LadybugDB columns; the typed domain stores them in `custom_properties`.

Core fields and relations cannot be replaced from this file. Removing or changing a
field that already exists in a published dump can make that dump incompatible; add
new optional fields instead.
