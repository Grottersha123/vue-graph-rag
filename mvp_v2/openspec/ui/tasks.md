# UI tasks

## Документация текущего состояния

- [x] Описать общую конфигурацию и зависимости UI.
- [x] Описать `Upload`.
- [x] Описать `Documents`.
- [x] Описать `Relations`.
- [x] Описать `Chunks & Embeddings`.
- [x] Описать `LadybugDB`.
- [x] Описать `Search`.
- [x] Описать `Chatbot`.
- [x] Описать `Errors`.

## Upload bootstrap — блокирующая проблема

- [x] Добавить явный current-schema bootstrap начальных `SourceType` и `Status` для пустого session snapshot.
- [x] Сделать сценарий «первый документ → первый dump → первая DB» выполнимым без retired import и заранее созданного dump.
- [x] Показывать отсутствующие `SourceType`/`Status` и действие инициализации рядом с Upload.
- [x] Сбрасывать устаревший ingestion preview при изменении любого входного поля или snapshot.
- [ ] Добавить UI-тест: пустой snapshot ясно объясняет отсутствие справочников.
- [ ] Добавить UI-тест: после bootstrap справочников валидный документ активирует `Preview`.
- [ ] Добавить сценарный тест: `Preview` → `Commit` → publish dump → build DB.


## Version upload и custom fields

- [x] Использовать один Upload-компонент во вкладке Upload и Documents dialog.
- [x] Привязать version upload к exact Document ID и locked current external ID.
- [x] Загружать Markdown sibling images тем же batch с version-confined Assets.
- [x] Скрыть raw `custom_properties` и развернуть configured fields в read-only колонки своего node type.
- [x] Защитить Commit fingerprint-привязкой preview к inputs, file hashes и snapshot revision.
- [ ] Рассмотреть content-addressed физическую дедупликацию одинаковых изображений между версиями; graph Asset nodes остаются version-owned.

## Общие UX-задачи

- [x] Показывать глобальное состояние: snapshot `dirty/clean`, выбранный dump и активную DB.
- [ ] Для каждой disabled основной кнопки показывать причину, а не только неактивное состояние.
- [ ] Проверить сброс зависимых session-state preview после смены dump, snapshot или DB.
- [ ] Унифицировать язык UI и сообщений об ошибках.
- [ ] Добавить smoke-тест навигации по всем восьми вкладкам.

## Bootstrap decision

Выбран explicit session-only action **Initialize workspace** с deterministic current-schema defaults. Автоматической мутации при старте и загрузки retired artifacts нет.
