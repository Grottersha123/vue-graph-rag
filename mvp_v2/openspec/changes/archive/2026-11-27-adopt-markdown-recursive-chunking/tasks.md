# Tasks

## 1. Production chunking

- [x] 1.1 Заменить внутреннюю реализацию default `split_text` на Markdown recursive 1200/120, сохранив `ChunkPolicy` и `make_chunks`; в свежем interpreter проверить детерминизм, nonblank output, лимит 1 200, plain-text fallback и стабильные hashes/IDs без создания новых tests.
- [x] 1.2 Прогнать существующие focused chunking/ingestion/backfill tests и подтвердить, что ingestion и regeneration используют один default entry point.

## 2. Combined documentation/FAQ pipeline

- [x] 2.1 Обновить `faq_rag_llm_judge_evaluation.ipynb`: после полного module reset импортировать production `split_text`, удалить собственный splitter документации и проверить свежую конструкцию DTO/service types.
- [x] 2.2 Перевести каждый отдельный FAQ Document на `chunking_strategy="default"`, убрать предположение «один FAQ = один chunk» и добавить runtime-инварианты числа 486 approved FAQ, уникальных `faq:` identity, metadata и непустых bounded chunks.
- [x] 2.3 Структурно перечитать notebook JSON и выполнить свежий preflight construction path без дорогой сборки; исходный `golden_dataset.json` и существующие evaluation cases не менять.

## 3. Изолированная пересборка и evaluation

- [x] 3.1 Зафиксировать current verified source dump/DB identity и подтвердить доступность исходников всех включённых current versions, включая 486 FAQ; при пропуске остановиться с `NOT VERIFIED`.
- [x] 3.2 Выполнить combined pipeline в новом каталоге `runtime/debug`, пересоздать chunks и chunk embeddings, опубликовать debug dump, повторно загрузить его и собрать отдельную LadybugDB; проверить snapshot, manifest, row counts и неизменность Document/Version/metadata/non-chunk graph data.
- [x] 3.3 Запустить неизменённую combined FAQ/documentation evaluation, сохранить run/results/metrics, сравнить с принятым baseline и подтвердить существующий quality gate либо зафиксировать `FAIL`/`NOT VERIFIED` без публикации.

## 4. Проверки и границы

- [x] 4.1 Выполнить Ruff, strict mypy и существующие relevant pytest suites в external project environment; новые test files/cases не создавать.
- [x] 4.2 Проверить `git diff --check`, scoped diff/status и hashes production dump/DB/config; сообщить пути debug artifacts и доказать, что `runtime/dumps`, `runtime/databases` и `.env` не изменены.

## Verification notes

- Combined evaluation: `quality_gate=PASS`, но общий status `NOT VERIFIED`: judge вернул два обрезанных JSON-ответа для documentation cases; production artifacts не публиковались.
- Strict mypy: `NOT VERIFIED` до анализа project code — установленный NumPy stub использует Python 3.12 type-statement syntax при project target Python 3.11.
- Focused pytest: 8 passed. Расширенный `test_delivery_ingestion_models.py`: 34 passed, 1 unrelated failure в сохранении `custom_properties` текущего dirty tree.
