# Tasks

## 1. Makefile workflow

- [x] 1.1 Add configurable Explorer engine, image, port, database root, and database name variables.
- [x] 1.2 Add a guarded interactive/non-interactive selector and read-only loopback container invocation.

## 2. Documentation

- [x] 2.1 Document Explorer usage, port override, Podman override, and immutable database protection in `README.md`.

## 3. Verification

- [x] 3.1 Verify help output and successful dry invocation for interactive and named selection using a harmless fake engine.
- [x] 3.2 Verify invalid name, missing artifact, and empty-root paths fail before engine invocation; run `git diff --check` and strict OpenSpec validation.
- [x] 3.3 Record host launch as `NOT VERIFIED`: Podman failed before Explorer startup with `proxy already running`; confirm the selected manifest and `database.lbug` hashes remained unchanged and do not claim browser acceptance.
