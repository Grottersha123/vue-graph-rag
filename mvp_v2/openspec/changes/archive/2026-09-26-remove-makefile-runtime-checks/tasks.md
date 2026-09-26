# Tasks

## 1. Makefile

- [x] 1.1 Remove `smoke`, `smoke-message`, `security`, and `verify` recipes and phony declarations.
- [x] 1.2 Keep deploy build, force-recreate, and readiness behavior while removing its `verify` call.

## 2. Documentation

- [x] 2.1 Update README deployment guarantees and available targets.

## 3. Verification

- [x] 3.1 Verify Make help no longer lists removed targets, deploy dry-run contains no removed target, diagnostics remain, and `git diff --check` plus strict OpenSpec validation pass.
