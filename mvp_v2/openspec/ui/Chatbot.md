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

Каждый успешный turn сохраняет вопрос, ответ и provenance: chat model/version, DB и source dump. Для ответа выводятся citations со score, chunk/asset, context, document, version, evidence ID и source.

- `Clear chat history` очищает session history.
- `Export chat as Markdown` появляется при непустой истории и формирует файл в памяти; на серверный диск файл не записывается.

Ошибка не добавляет непроверенный ответ в историю.
