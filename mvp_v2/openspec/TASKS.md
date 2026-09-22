# TASKS — живой список задач mvp_v2

Агент читает этот файл, AGENTS.md и делает ПЕРВУЮ незакрытую задачу из
«Текущая задача». Сделанное переносить в «Готово» с датой. Новые задачи
дописывать в конец «Текущая задача» или в «Бэклог».

## Текущая задача

- [ ] Следующая задача не выбрана

## Бэклог (сначала был текущей)

- Change 1 «add-dump-repair-restamp»: задачи 1.6–1.9 из
  `openspec/changes-2026-09-15.md` (файл
  `src/mvp_v2/application/dump_lifecycle/dump_restamp.py`, тесты
  `tests/test_dump_restamp.py`):
  1.6 precondition-хеши всех файлов + атомарный staging-swap с backup
  1.7 тесты stale-plan и write-failure (оригинал не тронут)
  1.8 guarded parsing до keyed-доступа (неверный заголовок = abort, не KeyError)
  1.9 иммутабельные entries плана + валидация dump_path
- Multi-position provenance в цитатах
- Multi-hop / bilingual retrieval eval
- Задокументировать cap 30 для standalone search

## Готово

- 2026-09-22 Change 8: RAG batch notebook, strict SOURCE provenance, async dated traces, chunk maintenance и pinned Ollama LAN transport — APPROVE
- 2026-09-21 Change 6: shared version upload, preview fingerprinting и type-specific custom columns — APPROVE
- 2026-09-21 Change 7: legacy loading полностью удалён; current-schema bootstrap и strict v2 artifacts — APPROVE
- 2026-09-21 `830dc17` schema v2: summaries, summary embeddings, 22 tables и configured optional fields (v1 compatibility позже retired Change 7)
- 2026-09-21 OpenSpec синхронизирован с schema v2 и текущим Change 6 version-upload/custom-columns
- 2026-09-20 Documents UI: единая таблица, сворачиваемый preview и row actions
- 2026-09-20 Upload preview: source provenance, Process/Role, HTTP(S) links
- 2026-09-20 Тесты Markdown-assets и безопасных upload-имён
- 2026-09-20 Upload: кириллица, пробелы и скобки в безопасных basename
- 2026-09-20 Upload: markdown-документ с локальными картинками одним Commit
- 2026-09-15 adc744d chat export Markdown (per-answer provenance)
- 2026-09-15 fe44299 lenient citation parsing
- 2026-09-15 d9d422d title backfill из заголовков
- 2026-09-15 f9db639 document-aware retrieval, DB+dump loader, RU chat

## Никогда без явной просьбы пользователя

- Chat autosave (отменён пользователем)
- Правки `mvp/`, `agentic_mvp/`, `.venv`, `.env*`, `*.lbdb`, `runtime/`-данных
