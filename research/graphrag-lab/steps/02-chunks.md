# Шаг 2 — чанки (60 мин)

Цель: превратить markdown-файлы в чанки и привязать к графу.

1. `experiments/step2_chunk.py`: режем `sources/*.md` на куски ~1200 символов по границам абзацев.
2. Таблица `Chunk(id INT64, doc_id STRING, text STRING, PRIMARY KEY(id))` и ребро `HAS_CHUNK(FROM Document, TO Chunk)`.
3. Проверка: у `student-onboarding` число чанков > 10; `MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) RETURN d.label, count(c);`
4. Открой 2–3 чанка глазами: осмысленные ли границы? Что ломается на таблицах?

Вопрос: почему нельзя искать по целым файлам? (подсказка: файл linkedin 12 КБ)
