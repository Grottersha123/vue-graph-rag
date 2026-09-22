# Chatbot

## Назначение

Ответы на вопросы только по evidence, найденным в активной LadybugDB, с provenance и citations.

## Настройки

- Search Ollama base URL.
- Evidence limit; дополнительно ограничивается системным `MAX_EVIDENCE_ITEMS`.
- `Authorize question embedding`.
- Точные chat model и model version.
- `Authorize answer generation` — отдельное разрешение генерации.
- Versioned prompt profile из `application/conversation/conf.py`; default — `support-v1`.
- Ollama generation temperature — `0.0` для детерминированной отладки.

Поле `Ask from retrieved evidence` доступно только при валидном runtime и актуальной активной DB. После ввода вопроса требуются обе authorizations и обе части identity chat-модели.

## История и экспорт

Каждый успешный turn сохраняет вопрос, ответ и provenance: chat model/version, DB и source dump. Модель использует короткие технические citations строгого формата `[N]` или `[N, M]` только внутри raw response. Application валидирует их и детерминированно заменяет перед показом пользователю на короткие ссылки: source URL при наличии, иначе document title, плюс человекочитаемый номер чанка. Те же ссылки без технической нумерации используются в Chat UI и Markdown export. Модель не генерирует source metadata и не управляет её порядком. Некорректная техническая citation вызывает одну повторную генерацию, а затем ошибку контракта. Retrieval передаёт в Chat только evidence с similarity score не ниже `0.55`; значение является частью request/trace metadata.

- `Clear chat history` очищает session history.
- `Export chat as Markdown` появляется при непустой истории и формирует файл в памяти; на серверный диск файл не записывается.

Ошибка не добавляет непроверенный ответ в историю.

## Ollama diagnostics

Transport различает bounded HTTP error detail, timeout и connection failure. Prompt payload в diagnostic message не включается. Loopback разрешён по умолчанию; LAN host требует exact allowlist `MVP_V2_OLLAMA_ALLOWED_LAN_HOSTS`, должен резолвиться только в private/link-local addresses, а HTTP connection закрепляется за уже проверенным IP без повторного DNS lookup или proxy. LAN HTTP не шифруется и предназначен только для явно принятого debug-режима. HTTP 404 с `model ... not found` означает, что exact model tag отсутствует в подключённом Ollama.
