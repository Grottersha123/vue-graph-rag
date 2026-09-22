# Vue Graph RAG UI

## Структура

Streamlit-приложение называется `Vue Graph RAG — local MVP` и содержит восемь вкладок:

1. [Upload](Upload.md)
2. [Documents](Documents.md)
3. [Relations](Relations.md)
4. [Chunks & Embeddings](Chunks-and-Embeddings.md)
5. [LadybugDB](LadybugDB.md)
6. [Search](Search.md)
7. [Chatbot](Chatbot.md)
8. [Errors](Errors.md)

Список выявленных изменений находится в [tasks.md](tasks.md).

## Sidebar: Local runtime paths

Во всех вкладках используется единая конфигурация:

- `Working data root`;
- `Dumps`;
- `Databases`;
- `Ladybug HOME`.

Невалидная конфигурация блокирует все операции. Отсутствующий каталог или symlink дополнительно блокирует операции, которым нужны файлы runtime.

## Session state

UI хранит в сессии:

- текущий graph snapshot;
- отдельные ingestion previews для основного Upload и version dialogs;
- revision snapshot для инвалидации previews;
- список обработанных ошибок;
- chat history;
- активную DB;
- manifest и путь выбранного dump;
- связь snapshot с dump run ID;
- признак dirty snapshot;
- планы удаления и ремонта dump.

Изменения документов, relations, chunks, embeddings или assets делают snapshot dirty и сбрасывают активную DB. Только публикация или загрузка verified dump возвращает snapshot в clean-состояние. Search и Chatbot работают только с актуальной DB, построенной из точного clean snapshot.

Каждая замена snapshot увеличивает session revision. Ingestion preview дополнительно связан с fingerprint полей и upload-файлов; изменение формы, файлов или revision блокирует commit устаревшего preview.

Configured properties из `graph_fields.json` показываются как отдельные read-only колонки только для соответствующего node type. Raw `custom_properties` — внутреннее представление и в UI не выводится.

## Общие правила операций

Долгие действия выполняются со spinner и записываются через Loguru в `runtime/logs/app.log`. Обработанные ошибки показываются пользователю без raw traceback и дублируются во вкладке `Errors`.
