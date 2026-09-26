# Context

Существующий `deploy` вызывает `verify`, который последовательно запускает Redis/DNS/auth/mount smoke, container-security checks и реальный RAG message request. Эти recipes увеличивают Makefile и делают обычный deploy зависимым от модели и end-to-end generation.

# Goals / Non-Goals

**Goals:** удалить выбранные test targets и сделать deploy ограниченным lifecycle/readiness.

**Non-Goals:** удаление Python tests, ослабление production authentication, изменение Compose hardening или удаление `health`/`diagnose-models` diagnostics.

# Decisions

- `deploy` по-прежнему выполняет config validation, build, force-recreate и bounded readiness wait.
- `health` и `diagnose-models` остаются явными diagnostics.
- README прямо сообщает, что deploy больше не доказывает authenticated RAG или security invariants.

# Risks / Trade-offs

Ошибки authentication, mounts, secret logging или полного RAG path больше не блокируют `make deploy`. Эти гарантии должны обеспечиваться отдельными tests или ручной operator verification.
