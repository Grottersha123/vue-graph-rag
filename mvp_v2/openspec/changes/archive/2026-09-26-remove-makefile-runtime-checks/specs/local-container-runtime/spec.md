# local-container-runtime Delta Specification

## MODIFIED Requirements

### Requirement: Deployment recreates stale application containers

`make deploy` SHALL rebuild and force-recreate Compose services and SHALL wait for the public backend readiness endpoint. It SHALL NOT run embedded smoke, authenticated message, or container-security test targets.

#### Scenario: Runtime contract changed

- **WHEN** Python runtime contracts or source code changed and the operator runs `make deploy`
- **THEN** backend and Telegram run the newly built image rather than a stale container
- **AND** deploy waits until the public backend health endpoint reports ready
- **AND** deploy completes without sending an authenticated RAG message or running embedded security tests
