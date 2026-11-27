# Design: Автоматический resumable batch runner

## Context

`run.ipynb` уже пишет batch-specific JSONL и manifest, но Cells 3–5 обрабатывают только выбранный batch. Пользователь должен вручную менять `BATCH_INDEX` и выполнять все cells. Это не подходит для длинного запуска и допускает manifest со статусом `parsed` без результатов.

## Decisions

### Automatic range

Cell 1 задаёт:

```python
BATCH_SIZE = 30
MAX_CASES = 1000
BATCH_INDEX = 1
AUTO_START_BATCH_INDEX = BATCH_INDEX
AUTO_BATCH_COUNT = None
```

`AUTO_START_BATCH_INDEX` — индекс первого batch, `AUTO_BATCH_COUNT` — число batches. `None` означает продолжить до `min(MAX_CASES, total_cases)`. Начальный case offset равен `AUTO_START_BATCH_INDEX * BATCH_SIZE`.

### Resume policy

Cell 6 считает batch completed только если manifest имеет `status=completed` и существуют cases, image descriptions, image errors, results, classification errors и merged errors artifacts. Такие batches пропускаются. Batches со статусом `parsed` или `failed` запускаются заново.

### Stage order

Для каждого batch runner выполняет строго:

1. выбрать deterministic range;
2. записать cases artifact и parsed manifest;
3. выполнить image descriptions и записать image artifacts/errors;
4. выполнить classification и записать results/errors;
5. объединить error inventory и обновить manifest на `completed`.

Fatal API error обновляет manifest на `failed` с `failed_stage` и останавливает цикл. Per-case failures остаются в error artifacts и не останавливают следующий batch.

### Protected artifacts

Успешный batch не перезаписывается. Незавершённые artifacts могут быть пересозданы при повторном запуске этого batch. Все model calls выполняются только для выбранного batch.

## Risks

- Полный запуск до 1000 cases может занять много часов из-за image model calls; progress bars и manifests показывают текущее состояние.
- При остановке процесса текущий batch может иметь статус `parsed` или `failed`; следующий запуск безопасно повторяет его.
