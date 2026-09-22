# Upload

## Назначение

`Upload` — основная точка ingestion для URL и локальных файлов. Один и тот же компонент формы используется обычной вкладкой и диалогом **Upload new version** из Documents. Отдельного ingestion-процесса во вкладке Documents нет.

Операция разделена на два шага:

1. `Preview` извлекает и проверяет входные данные без изменения snapshot.
2. `Commit` применяет только точный актуальный preview к session snapshot и записывает confined runtime-файлы.

Dump и LadybugDB автоматически не публикуются.

## Поля

- `Source kind`: `url`, `pdf`, `markdown`, `html`, `image_collection`.
- `SourceType` и `Required Status`: обязательные ссылки на справочники snapshot.
- `Process` и `Role`: необязательные ссылки; обычный Upload предпочитает `onboarding` и `student` при их наличии.
- `Title`: для Markdown необязателен; fallback — первый корректный H1 вне code/comment contexts, затем stem файла.
- `Existing/new identity (external_id)`: идентичность документа.
- `Source URL`: обязателен для `url`, для локального файла служит optional provenance.
- `Local input`: один или несколько файлов для локальных source kind.

Если `external_id` отсутствует, идентичность выбирается как `source_url`, затем `local:<input_path>`. `image_collection` требует явный `external_id` или Source URL.

## Markdown и изображения

Markdown-файл и локальные sibling-изображения передаются одним upload batch. Извлекатель:

- рассматривает только безопасные basename-ссылки;
- декодирует URL-escaped имена и поддерживает Unicode;
- дедуплицирует совпадения;
- прикладывает не более 32 уникальных локальных изображений;
- HTTP(S)-ссылки только показывает в `Potential downloads`, не скачивает и не проверяет сервером.

Assets новой версии принадлежат только её `DocumentVersion` и получают version-confined runtime paths. Assets и файлы прошлых версий не перезаписываются.

## Preview integrity

Preview связан с fingerprint всех входов формы:

- kind, title и выбранные relation IDs;
- external ID, source URL и target Document ID;
- имена и SHA-256 содержимого всех upload-файлов;
- revision текущего session snapshot.

Изменение любого входа или snapshot делает preview устаревшим и блокирует `Commit` до нового `Preview`. Перед каждой новой попыткой старый preview очищается, поэтому ошибка preview не оставляет предыдущий результат доступным для commit.

Локальные файлы временно записываются в confined staging directory и удаляются после preview. Успешный preview показывает Document ID, номер новой версии, SHA-256, число chunks и assets.

## Commit

Commit доступен только для preview с текущим fingerprint. Успех:

- создаёт или обновляет Document;
- добавляет новый immutable `DocumentVersion`;
- делает предыдущую версию historical;
- сохраняет существующие `Document.summary` и configured custom properties при re-ingestion;
- помечает snapshot dirty и сбрасывает активную DB.

Runtime-файл с тем же путём принимается повторно только при byte-identical содержимом. Отличающийся файл, symlink или non-file не перезаписывается.

## Empty-snapshot bootstrap

Если snapshot не содержит `SourceType` или `Status`, UI показывает точный список отсутствующих reference tables и кнопку **Initialize workspace**. Явное действие добавляет только недостающие deterministic current-schema defaults в session snapshot, помечает его dirty и не читает файлы, не публикует dump и не создаёт DB.
