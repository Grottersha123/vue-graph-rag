# Spec Delta

## Purpose

Предоставляет явный и проверяемый процесс подготовки уже принятого GraphSnapshot к RAG-поиску через chunks, vision descriptions и embeddings без скрытых model side effects.

## ADDED Requirements

### Requirement: Chunk preparation is explicit

The system SHALL provide an explicit preparation action that reads verified current sources and adds deterministic Chunks and ownership relations to an already accepted GraphSnapshot.

#### Scenario: Prepare chunks for a current Markdown version

- **WHEN** an explicit chunk preparation action runs for a current Markdown version whose source path and SHA-256 are valid
- **THEN** the system creates deterministic Chunk nodes and `HAS_CHUNK` relations without changing unrelated documents, versions, assets or relations

#### Scenario: Ingestion has not prepared chunks

- **WHEN** a newly committed text version has no Chunks
- **THEN** the explicit preparation action can build its chunks later from the verified stored source

### Requirement: Preparation verifies source integrity

The system SHALL reject chunk preparation when the source is missing, changed, not valid UTF-8 for text chunking, or cannot be safely resolved under the configured media root.

#### Scenario: Source changed after ingestion

- **WHEN** the stored source bytes do not match the DocumentVersion SHA-256
- **THEN** preparation fails, reports the affected version and leaves the candidate snapshot unchanged

#### Scenario: Unsupported current source

- **WHEN** a current non-Markdown version has existing chunks but no supported source reconstruction path for regeneration
- **THEN** destructive regeneration is rejected instead of silently deleting those chunks

### Requirement: Model preparation is explicit and authorized

The system SHALL perform vision description and embedding operations only after explicit authorization and SHALL preserve model identity, vector dimensions and embedding history rules.

#### Scenario: Unauthorized model operation

- **WHEN** a vision or embedding operation is requested without matching explicit authorization
- **THEN** the operation is rejected before calling the model provider and the snapshot remains unchanged

#### Scenario: Build missing embeddings

- **WHEN** an authorized missing-embedding operation is requested for a valid embedding identity
- **THEN** the system creates only missing current embeddings and preserves existing embedding history

#### Scenario: Rebuild all embeddings

- **WHEN** an authorized rebuild-all operation is requested
- **THEN** relevant current embeddings are retired, a new deterministic generation is added, and typed ownership relations remain valid

#### Scenario: Invalid model output

- **WHEN** the provider returns the wrong number, dimension or non-finite values for vectors, or an empty vision description
- **THEN** the operation fails without returning a partially updated snapshot

### Requirement: Preparation preserves graph invariants

The system SHALL validate the candidate GraphSnapshot after chunk, vision or embedding preparation and SHALL reject any result that violates the canonical graph invariants.

#### Scenario: Preparation would create an invalid graph

- **WHEN** a preparation result has invalid ownership, duplicate IDs or invalid typed relations
- **THEN** the operation fails and the previous snapshot remains available unchanged
