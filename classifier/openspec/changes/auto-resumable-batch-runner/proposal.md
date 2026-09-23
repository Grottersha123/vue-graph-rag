# Proposal: Автоматический resumable batch runner

## Why

Текущий notebook выбирает и обрабатывает только один batch за запуск. Если выполнить Cell 3, но не выполнить Cells 4–5, в manifest остаётся `parsed`, а image/classification artifacts не создаются. Для набора из 1944 cases это легко приводит к пропущенным batches.

## What Changes

- Добавить автоматический Cell 6 для последовательной обработки batches.
- Добавить настройки `AUTO_START_BATCH_INDEX` и `AUTO_BATCH_COUNT`.
- При `AUTO_BATCH_COUNT=None` обрабатывать все batches до `MAX_CASES`.
- Пропускать только batches со статусом `completed` и полным набором artifacts.
- Обрабатывать последний короткий batch до `MAX_CASES`.
- Останавливать цикл при fatal image/text API error и сохранять failed manifest.

## Non-Goals

- Не менять taxonomy, DTO, prompt semantics, model names или endpoint URLs.
- Не запускать автоматически batches сверх `MAX_CASES`.
- Не скрывать per-case ошибки классификации или описания изображений.

## Acceptance

- При `BATCH_SIZE=30`, `MAX_CASES=1000`, `AUTO_START_BATCH_INDEX=1` runner выбирает `[30:60]` и далее последовательные ranges до `[990:1000]`.
- Завершённые batches не перезапускаются и не перезаписываются.
- Последний batch содержит 10 cases.
- Controlled run с 65 cases создаёт batches `[30, 30, 5]` и обновляет manifests.
- OpenSpec validation, notebook AST, Ruff и `git diff --check` проходят.
