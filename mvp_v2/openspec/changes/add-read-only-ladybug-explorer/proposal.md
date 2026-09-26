# Why

Оператору нужен быстрый способ открыть уже подготовленную LadybugDB из `runtime/databases/` в официальном browser-based Explorer. Ручная сборка `docker run` легко приводит к выбору неверного пути, публикации порта на всех интерфейсах или открытию immutable artifact в режиме записи.

# What Changes

- Добавить Makefile target `explorer`, который выбирает существующий canonical `.lbdb` artifact интерактивно или через переменную.
- Запускать официальный `ghcr.io/ladybugdb/explorer:latest` через project Podman command с loopback-only портом, read-only bind mount, `MODE=READ_ONLY` и сохранением host UID.
- Разрешить переопределять совместимый container engine и host port без изменения Makefile.
- Документировать запуск, выбор базы и конфликт порта с gateway.

# Capabilities

## Modified Capabilities

- `local-container-runtime`: добавить безопасный operator workflow для просмотра существующей LadybugDB.

# Impact

Изменяются только `Makefile`, `README.md` и OpenSpec artifacts. Production Python, Compose deployment, database contents и legacy-папка `mvp/` не изменяются.
