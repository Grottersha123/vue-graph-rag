# Tasks: Автоматический resumable batch runner

## 1. Notebook runner

- [x] 1.1 Добавить `AUTO_START_BATCH_INDEX` и `AUTO_BATCH_COUNT`.
- [x] 1.2 Реализовать последовательный runner для ranges по 30 cases.
- [x] 1.3 Пропускать только completed batches с полным набором artifacts.
- [x] 1.4 Обрабатывать final short batch и обновлять manifest.
- [x] 1.5 Останавливать цикл на fatal API error с failed manifest.

## 2. Verification

- [x] 2.1 Проверить notebook AST.
- [x] 2.2 Controlled-run проверить ranges `[30, 30, 5]` и resume behavior.
- [ ] 2.3 Выполнить реальный финальный короткий batch и проверить aggregate error inventory.
- [x] 2.4 Запустить Ruff после согласования существующих `promt.py` style findings.
