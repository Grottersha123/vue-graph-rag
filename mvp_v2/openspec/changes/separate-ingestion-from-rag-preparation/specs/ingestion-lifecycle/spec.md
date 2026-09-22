# Spec Delta

## Purpose

Отделяет приём исходного материала и жизненный цикл версий документов от последующей подготовки RAG-артефактов, чтобы загрузка была предсказуемой и явно управляемой.

## ADDED Requirements

### Requirement: Source ingestion is independent from RAG preparation

The system SHALL allow a valid ingestion commit to create or update the Document, its DocumentVersion, source file and extracted Assets without creating Chunks or Embeddings as an implicit side effect.

#### Scenario: Commit a text document

- **WHEN** a user previews and commits a valid Markdown, PDF, HTML or URL input
- **THEN** the system creates the document/version and stores the source and extracted assets, and the commit performs no chunking, vision or embedding model operation

#### Scenario: Commit an image collection

- **WHEN** a user previews and commits a valid image collection
- **THEN** the system creates the document/version and Asset nodes, and no Chunk or Embedding is created by ingestion

### Requirement: Ingestion preserves version and source integrity

The system SHALL keep the existing document identity, version numbering, source SHA-256 and confined media paths while separating RAG preparation from ingestion.

#### Scenario: Upload a new version for an existing document

- **WHEN** the request targets an existing Document with matching external identity
- **THEN** ingestion creates the next DocumentVersion, preserves earlier versions and assets, and does not attach preparation artifacts to the new version implicitly

#### Scenario: Stale or mismatched preview

- **WHEN** the input, target identity or snapshot changes after preview and before commit
- **THEN** the commit is rejected and neither the snapshot nor runtime files are changed

### Requirement: Ingestion failures are isolated

The system SHALL leave the candidate snapshot unchanged when extraction, identity validation, file validation or graph validation fails during ingestion.

#### Scenario: Invalid source input

- **WHEN** extraction or graph validation fails before a successful commit
- **THEN** the system returns an actionable error and does not publish a partial document version, Chunk, Embedding or media set
