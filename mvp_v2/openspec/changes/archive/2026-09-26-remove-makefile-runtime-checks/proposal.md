# Why

Gateway Makefile смешивает deployment с embedded smoke, end-to-end RAG и container-security test workflows. Оператор хочет оставить в Makefile запуск сервисов и явные diagnostics, а тесты выполнять отдельно.

# What Changes

- Удалить targets `smoke`, `smoke-message`, `security` и `verify`.
- Изменить `make deploy`: rebuild, force-recreate и ожидание `/health` сохраняются, автоматический test workflow удаляется.
- Обновить README, чтобы он не обещал authenticated RAG/security verification во время deploy.

# Capabilities

## Modified Capabilities

- `local-container-runtime`: deployment заканчивается после readiness, без встроенной полной verification suite.

# Impact

Меняются `Makefile`, `README.md` и OpenSpec artifacts. Python tests, production code, secrets, databases и volumes не изменяются. Удаление проверок снижает автоматические post-deploy гарантии и является осознанным operator trade-off.
