# Search

## Назначение

Векторный поиск evidence в активной LadybugDB через embedding вопроса в Ollama.

## Поля

- `Search question`;
- `Search Ollama base URL`;
- `Evidence limit` от 1 до 100, по умолчанию 8;
- `Authorize question embedding`.

## Действие

`Search evidence` активна, когда есть валидная runtime configuration, актуальная активная DB, непустой вопрос и явное разрешение embedding. Model identity берётся из manifest активной DB.

## Результат

Для каждого evidence показываются текст, score, позиция chunk либо `asset`, context scope, document ID, version, embedding ID и source. Результат хранится в session state и сбрасывается при изменениях snapshot, которые делают DB неактуальной.
