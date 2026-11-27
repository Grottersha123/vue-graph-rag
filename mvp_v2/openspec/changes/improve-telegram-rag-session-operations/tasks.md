# Tasks

## 1. Contracts and settings

- [x] 1.1 Добавить settings для evidence limit, log level, session inactivity и Selora URL/key; проверить startup validation в свежем интерпретаторе.
- [x] 1.2 Обновить `.env.example` с безопасными пояснениями `gateway_token`, token-file и local-only значений; проверить отсутствие реальных секретов.

## 2. Telegram UX and session

- [x] 2.1 Реализовать завершённое состояние profile selection и удаление/disable клавиатуры; проверить повторный callback.
- [x] 2.2 Исправить skip flow: callback acknowledgement, очистка state и видимое продолжение; проверить Telegram controller scenario.
- [x] 2.3 Добавить inactivity greeting и Redis TTL state; проверить новые и возвращающиеся сессии.

## 3. RAG provider and retrieval

- [x] 3.1 Реализовать Selora adapter и primary provider selection; проверить request contract и redaction.
- [x] 3.2 Реализовать fallback на Gemma для timeout/transport/provider errors и audit status; проверить, что fallback не происходит молча.
- [x] 3.3 Добавить Telegram notification о fallback; проверить отсутствие внутренних ошибок/секретов в сообщении.
- [x] 3.4 Исключить телефонные номера из RAG evidence, сохранить доступность подходящих legacy-документов без новых metadata и проверить evidence limit.

## 4. Diagnostics and verification

- [x] 4.1 Добавить ERROR/DEBUG logging configuration и redaction; проверить container-visible events.
- [x] 4.2 Сделать короткую поддержанную операцию export logs и проверить права/состав архива.
- [x] 4.3 Запустить focused checks, `git diff --check` и проверить, что `.env`, secrets и databases не попали в artifacts.
