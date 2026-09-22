# LadybugDB

## Назначение

Управление immutable dumps и LadybugDB: загрузка snapshot, публикация dump, ремонт вручную изменённого dump, выбор существующей DB и построение новой.

## Dumps

`Verified dump candidate` содержит каталоги вида `dump-NNNNNN` из configured dumps root.

- `Load selected canonical dump` проверяет manifest и содержимое, затем привязывает snapshot к dump и сбрасывает активную DB.
- `Publish current snapshot` требует checkbox `Confirm publish current snapshot as next immutable dump`; создаёт следующий immutable dump и делает snapshot clean.

## Repair manually edited dump

1. `Check edited dump` строит read-only repair plan.
2. UI показывает причины abort, сообщение об отсутствии изменений или список планируемых исправлений.
3. Для записи нужен checkbox `Apply these repairs (rows will be deleted)`.
4. `Apply dump re-sign` применяет план и повторно проверяет dump.

Безопасная атомарность, stale-plan preconditions и immutable plan описаны как незавершённые задачи Change 1 в основном OpenSpec.

## Existing databases

Для каждого verified `.lbdb` показываются artifact, build ID, row counts, stale-состояние, embedding identity и vision identity.

`Load selected DB with its source dump` загружает точный source dump, проверяет соответствие manifest и активирует DB. Несовпадающая или stale DB не активируется.

## Build new database

Поля:

- safe database name: латиница/цифра в начале, далее латиница, цифры, `_` или `-`, максимум 64 символа;
- точные embedding model/version и dimension;
- точные vision model/version;
- checkbox `Confirm exact new DB build`.

`Save as new DB` активна, только когда выбранный dump уже загружен, snapshot clean, runtime-каталоги валидны, имя и model identities заполнены, confirmation включён. Успех строит, проверяет и выбирает новую DB.

Внизу показывается команда provision vector extension для текущего `Ladybug HOME`.
