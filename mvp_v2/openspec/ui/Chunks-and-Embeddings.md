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

## Destructive chunk maintenance

Два session-only действия отделены от обычного backfill:

- `Preview regenerate all chunks` сначала удаляет весь chunk-контур и owned chunk embeddings из candidate, затем проверяет SHA-256 current Markdown sources и строит chunks текущей policy. Asset nodes и asset embeddings сохраняются. Совпадающие chunk-to-asset edges сохраняются; unsupported current non-Markdown chunks блокируют операцию.
- `Preview delete all chunks and chunk embeddings` удаляет все Chunk, HAS_CHUNK, CONTAINS_ASSET, HAS_EMBEDDING_CHUNK и принадлежащие им Embedding rows. Documents, Versions, Assets, asset/summary embeddings и остальные relations сохраняются.

Оба preview показывают counts, привязаны к `snapshot_revision` и требуют отдельный destructive checkbox. Commit меняет только session snapshot. Dump и DB создаются позже обычными publish/build действиями.

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
