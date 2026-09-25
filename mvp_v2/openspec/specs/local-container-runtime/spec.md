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

`make deploy` SHALL rebuild and force-recreate Compose services before readiness and authenticated message verification.

#### Scenario: Runtime contract changed

- **WHEN** Python runtime contracts or source code changed and the operator runs `make deploy`
- **THEN** backend and Telegram run the newly built image rather than a stale container

### Requirement: Clean removes containers without deleting persistent data

`make clean` SHALL remove Compose containers and network while preserving images, named volumes, secrets, `.env`, and database artifacts.

#### Scenario: Operator cleans the local deployment

- **WHEN** the operator runs `make clean`
- **THEN** Compose containers and network are removed
- **AND** Redis data and gateway JSONL named volumes remain available
- **AND** secrets, `.env`, images, and Ladybug database artifacts are not deleted
