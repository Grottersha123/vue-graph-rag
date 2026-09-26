# Context

Все current database artifacts находятся как непосредственные каталоги `runtime/databases/<name>.lbdb/` и содержат `database.lbug` и `_mvp_v2_manifest.json`. Официальный Ladybug Explorer по умолчанию использует `READ_WRITE`, поэтому прямой пример из документации нарушил бы immutable database boundary проекта.

# Goals / Non-Goals

**Goals:** безопасный выбор существующего artifact, локальный browser access и отсутствие записи в database.

**Non-Goals:** создание database, изменение schema/data, автоматический запуск browser, управление lifecycle gateway или pinning внешнего image digest.

# Decisions

- Target принимает только непосредственные real directories `runtime/databases/*.lbdb` с real files `database.lbug` и `_mvp_v2_manifest.json`; symlinks и произвольные paths исключаются.
- По умолчанию используется project `PODMAN` command; для Podman передаётся `--userns=keep-id`, рекомендованный официальной документацией Explorer для bind mounts. Совместимый engine можно явно переопределить через `EXPLORER_ENGINE`.
- Artifact монтируется `:ro`, а Explorer получает `MODE=READ_ONLY` для двух независимых уровней защиты.
- Публикация ограничена `127.0.0.1`. Default host port — `8000`; при работающем gateway оператор задаёт `EXPLORER_PORT=8001`.
- Интерактивный selector дополняется `EXPLORER_DATABASE=<artifact-name>` для автоматизации и тестирования.

# Risks / Trade-offs

- Mutable tag `latest` может измениться; image вынесен в `EXPLORER_IMAGE`, чтобы оператор мог передать pin.
- Explorer и gateway конкурируют за port 8000; README показывает override.
- Static validation не подтверждает host container runtime. Host launch был `NOT VERIFIED`: Podman на macOS завершился до старта Explorer с известной ошибкой stale `gvproxy` (`proxy already running`); hashes выбранного artifact остались неизменны.
