# local-container-runtime Specification

## Purpose
Define the reproducible and data-safe local Podman Compose workflow for the gateway.

## Requirements

### Requirement: Container image provides gateway runtime prerequisites

The shared backend and Telegram image SHALL include bounded `curl` diagnostics and SHALL provision the Ladybug `vector` extension at build time in an image-owned HOME. Runtime SHALL only load the pre-provisioned extension and SHALL NOT install it or mutate the selected database artifact.

#### Scenario: Backend performs ANN retrieval in the image

- **WHEN** a message request reaches Ladybug ANN retrieval
- **THEN** `LOAD vector` succeeds without a runtime extension download
- **AND** the selected `.lbdb` artifact remains read-only

#### Scenario: Operator checks backend health

- **WHEN** the operator runs `make health` or bounded in-container `curl`
- **THEN** the backend health response is printed
- **AND** the command has a finite timeout

### Requirement: Deployment recreates stale application containers

`make deploy` SHALL rebuild and force-recreate Compose services and SHALL wait for the public backend readiness endpoint. It SHALL NOT run embedded smoke, authenticated message, or container-security test targets.

#### Scenario: Runtime contract changed

- **WHEN** Python runtime contracts or source code changed and the operator runs `make deploy`
- **THEN** backend and Telegram run the newly built image rather than a stale container
- **AND** deploy waits until the public backend health endpoint reports ready
- **AND** deploy completes without sending an authenticated RAG message or running embedded security tests

### Requirement: Clean removes containers without deleting persistent data

`make clean` SHALL remove Compose containers and network while preserving images, named volumes, secrets, `.env`, and database artifacts.

#### Scenario: Operator cleans the local deployment

- **WHEN** the operator runs `make clean`
- **THEN** Compose containers and network are removed
- **AND** Redis data and gateway JSONL named volumes remain available
- **AND** secrets, `.env`, images, and Ladybug database artifacts are not deleted

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
