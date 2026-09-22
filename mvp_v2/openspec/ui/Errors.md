# Errors

## Назначение

Последняя вкладка показывает обработанные ошибки текущей сессии и application log. Legacy import/report controls удалены.

## Содержимое

- список безопасных user-facing ошибок из session state;
- путь к `runtime/logs/app.log`;
- последние строки application log;
- скачивание текущего log как `mvp-v2-app.log`.

Raw traceback, секреты и содержимое `.env` в UI не выводятся. Вкладка не изменяет snapshot, dump, database или runtime media.
