# Шаг 1 — граф MVP в базу (60 мин)

Цель: залить `veu-graph-mvp.json` и впервые спросить базу о своих данных.

1. Скрипт `experiments/step1_load.py`: создать таблицы
   `Process(id STRING, label STRING)`, `Document(id, label, path)`, `System(id, label)`, `Source(id, label)`.
2. Узлы из JSON: type -> таблица. PRIMARY KEY = id.
3. Рёбра: по типам пар (например `BASED_ON(FROM Process, TO Document)`, `NEXT_STEP(Process, Process)`, `USES(Process, System)`).
4. Проверки:
   - `MATCH (p:Process) RETURN count(*);` — ожидание 10.
   - `MATCH (d:Document) RETURN count(*);` — ожидание 18 (после telnumber).
   - Список файлов процесса 2: `MATCH (:Process {id:'process:2'})-[:BASED_ON]->(d:Document) RETURN d.label;`
5. Вопрос: как выглядит этот же ответ в [[VEU — граф файлов и процессов (MVP онбординг)]]? Совпадает ли?

Подводный камень: rёбра в JSON с парами (Role->Process, Document->Process DESCRIBES) — понадобится таблица на каждую комбинацию типов концов. Заметить, сколько вышло, и записать в FINDINGS.
