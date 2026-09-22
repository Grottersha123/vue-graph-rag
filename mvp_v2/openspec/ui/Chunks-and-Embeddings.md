# Chunks & Embeddings

## Назначение

Создание отсутствующих chunks, генерация embeddings и описание изображений через локальный Ollama.

## Таблицы узлов

Вкладка показывает отдельные таблицы Chunk, Asset и Embedding. Для Embedding дополнительно показывается `current` или `historical`.

Внутренняя колонка `custom_properties` нигде не показывается. Поля из `graph_fields.json` разворачиваются в отдельные read-only колонки только своей таблицы:

- Chunk fields — только Chunk;
- Asset fields — только Asset;
- Embedding fields — только Embedding.

Необъявленные поля остаются ошибкой схемы.

## Text chunks

`Create missing text chunks` читает media из confined working root, создаёт недостающие chunks и обновляет session snapshot. Если создавать нечего, snapshot не меняется.

## Embeddings

Поля операции:

- Ollama base URL и фиксированный provider `ollama`;
- exact model и model version;
- dimension и generation token;
- batch size от 1 до 256;
- явное разрешение `Authorize this embedding operation`.

`Build missing embeddings` создаёт отсутствующие current embeddings. `Rebuild all embeddings` деактивирует старые и создаёт новые. Chunk `vector` строится из content, а непустой `summary_vector` — из Chunk.summary. Document.summary получает отдельный embedding через `HAS_SUMMARY_EMBEDDING`; он сохраняется в dump, но исключён из active Ladybug ANN projection. Asset embedding строится из `Asset.description`.

## Vision description

Пользователь выбирает Asset, указывает exact vision model/version и даёт явное разрешение. Media проверяется по SHA-256 до вызова Ollama; новое description меняет только session snapshot.

## Ошибки

При ошибке model identity, runtime, media, Ollama или graph validation snapshot не изменяется. Долгие операции выполняются через spinner и application log.
