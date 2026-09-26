# local-container-runtime Delta Specification

## ADDED Requirements

### Requirement: Operator opens an existing database in read-only Explorer

The Makefile SHALL provide an `explorer` target that selects only an existing canonical database artifact directly under `runtime/databases/` and SHALL launch the official Ladybug Explorer without permitting database mutation.

#### Scenario: Operator selects an artifact interactively

- **WHEN** the operator runs `make explorer` and chooses a listed artifact
- **THEN** only real `.lbdb` directories containing `database.lbug` and `_mvp_v2_manifest.json` are offered
- **AND** the selected artifact is mounted read-only
- **AND** Explorer starts with `MODE=READ_ONLY`

#### Scenario: Operator selects an artifact non-interactively

- **WHEN** the operator supplies a valid `EXPLORER_DATABASE` artifact name
- **THEN** that exact artifact is opened without an interactive prompt

#### Scenario: Selection is invalid or no artifact exists

- **WHEN** the database name is unsafe, absent, a symlink, or lacks required canonical files
- **THEN** the target fails before invoking the container engine

### Requirement: Explorer exposure is local and configurable

The Explorer target SHALL publish its HTTP service only on loopback and SHALL allow the operator to override the container engine, host port, and image.

#### Scenario: Operator uses defaults

- **WHEN** the operator launches Explorer without overrides
- **THEN** the project Podman command runs `ghcr.io/ladybugdb/explorer:latest` with the host user namespace retained
- **AND** Explorer is available through `127.0.0.1:8000`

#### Scenario: Gateway already occupies port 8000

- **WHEN** the operator sets `EXPLORER_PORT=8001`
- **THEN** the Explorer container publishes port 8000 as `127.0.0.1:8001`
