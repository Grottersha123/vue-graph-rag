# Chatbot

## Назначение

Ответы на вопросы только по evidence, найденным в активной LadybugDB, с provenance и citations.

## Настройки

- Search Ollama base URL.
- Evidence limit; дополнительно ограничивается системным `MAX_EVIDENCE_ITEMS`.
- `Authorize question embedding`.
- Точные chat model и model version.
- `Authorize answer generation` — отдельное разрешение генерации.

Поле `Ask from retrieved evidence` доступно только при валидном runtime и актуальной активной DB. После ввода вопроса требуются обе authorizations и обе части identity chat-модели.

## История и экспорт

Каждый успешный turn сохраняет вопрос, ответ и provenance: chat model/version, DB и source dump. Для ответа выводятся citations со score, chunk/asset, context, document, version, evidence ID и source. Production prompt передаёт для каждого evidence точную `SOURCE [N]` строку с document title/id, version, человекочитаемым `chunk_ordinal`, сохранённым `chunk_position`, общим числом chunks и source reference. Модель обязана сослаться на evidence в теле ответа и дословно вернуть строки использованных citations внутри единственного завершающего блока `<SOURCES>...</SOURCES>`. Parser не учитывает citation tokens из source-блока, отклоняет неизвестные, дублированные и расширенные SOURCE-строки; нарушение вызывает одну повторную генерацию, а затем ошибку контракта.

- `Clear chat history` очищает session history.
- `Export chat as Markdown` появляется при непустой истории и формирует файл в памяти; на серверный диск файл не записывается.

Ошибка не добавляет непроверенный ответ в историю.

## Ollama diagnostics

Transport различает bounded HTTP error detail, timeout и connection failure. Prompt payload в diagnostic message не включается. Loopback разрешён по умолчанию; LAN host требует exact allowlist `MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS`, должен резолвиться только в private/link-local addresses, а HTTP connection закрепляется за уже проверенным IP без повторного DNS lookup или proxy. LAN HTTP не шифруется и предназначен только для явно принятого debug-режима. HTTP 404 с `model ... not found` означает, что exact model tag отсутствует в подключённом Ollama.
