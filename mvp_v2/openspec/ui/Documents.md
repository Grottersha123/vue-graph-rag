# Documents

## Назначение

Documents — основная таблица документов, metadata editing, preview, загрузка новой версии, title backfill и file-first deletion.

## Основная таблица

В одной таблице показываются:

- выбор одной строки;
- `id`, `title`, `source_url`, `external_id`, `summary`, `is_active`;
- формат, путь и номер current version;
- числа chunks, assets, входящих и исходящих links;
- configured custom fields типа `Document` как отдельные read-only колонки.

Редактируются только `title`, `source_url`, `external_id`, `summary` и `is_active`. Перед применением UI показывает `Pending changes` с before/after. `Commit changes` меняет только session snapshot. Изменение summary инвалидирует current document-summary embedding, чтобы `Build missing embeddings` создал новый.

Raw `custom_properties` не отображается. Поля, объявленные для Chunk, Asset или Embedding, в Documents не появляются.

## Preview и source action

Для одной выбранной строки показывается current version, текст, source и Assets. Asset custom fields разворачиваются в обычные колонки; внутренний sidecar скрыт. HTTP(S) source URL можно открыть отдельной кнопкой.

Документ без current version остаётся видимым и получает явное предупреждение.

## Upload new version

Для выбранного документа кнопка **Upload new version** открывает dialog с тем же upload-компонентом, что используется во вкладке Upload.

Диалог:

- фиксирует точный `document_id`;
- заново подставляет текущий `external_id` и блокирует его редактирование;
- подставляет текущие SourceType, Status, Process и Role при наличии;
- очищает старые widget values, файлы и preview при каждом явном открытии;
- использует общий Preview → Commit workflow.

Application service проверяет, что `document_id` существует и переданный `external_id` совпадает с выбранным Document. Поэтому новая версия не может случайно присоединиться к другому документу. После commit история старых версий сохраняется.

Для Markdown пользователь выбирает `.md` и все нужные sibling-изображения одним batch. Новая версия получает новые Asset nodes и version-confined paths; старые Assets остаются частью истории. Физическая дедупликация одинаковых изображений между версиями пока не выполняется.

## Fix generated titles

Действие исправляет только автоматически сгенерированные технические названия. Изменения применяются к session snapshot и требуют последующей публикации.

## File-first deletion

1. Выбрать ровно один Document.
2. Создать deletion preview, связанный с текущим dump run ID и identity working root.
3. Проверить graph/file impact и подтвердить точный preview.
4. Опубликовать новый immutable dump без документа.
5. Только после публикации удалить проверенные физические файлы.

Перед unlink проверяются confinement, отсутствие symlink/traversal и ожидаемый SHA-256. Shared graph paths не удаляются. Активная LadybugDB для удаления не обязательна; после успеха она сбрасывается.
