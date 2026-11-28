# Design

## Context

Текущий Telegram-бот хранит только pending feedback в Redis, а выбор профиля и отправка сообщений реализованы отдельными обработчиками. RAG использует локальный model path и retrieval evidence из активной базы. Compose уже передаёт backend token через secret file и имеет отдельный volume для логов.

## Goals / Non-Goals

**Goals:**
- Сделать пользовательские состояния Telegram явными и идемпотентными.
- Добавить provider boundary с наблюдаемым fallback без скрытой подмены модели.
- Ограничить evidence конфигурацией и фильтрами scope.
- Дать оператору понятные уровни логов и короткую команду экспорта.

**Non-Goals:**
- Не хранить секреты в git, OpenSpec artifacts или обычных логах.
- Не менять graph schema и не индексировать сырые чаты как FAQ.
- Не добавлять systemd или deployment automation в этот change.

## Decisions

### Явное состояние Telegram

Состояние профиля, feedback и последней активности хранить в Redis с TTL и request identity. После успешного выбора профиля inline-клавиатуру удалять или заменять подтверждением; повторный callback не должен повторно выполнять действие. «Пропустить» должен отвечать callback acknowledgement, очищать pending state и отправлять следующий вопрос/подсказку.

### Session greeting

При `/start` и при первом сообщении после настраиваемого периода inactivity отправлять короткое приветствие и текущую подсказку. Таймер не должен сбрасываться самим исходящим сообщением бота; состояние обновляется только после принятого пользовательского сообщения.

### Provider chain

Ввести единый provider result с выбранным provider, fallback reason и request id. Selora вызывается первым при наличии настроенных URL/key. При transport, timeout или provider error выполняется Gemma; fallback разрешён только для этой цепочки и фиксируется в audit. Telegram получает нейтральное уведомление о временном использовании резервной модели, без секретов и внутренних stack trace.

### Retrieval safety

`evidence_limit` должен проходить через settings, backend request и search boundary. Добавить negative checks, которые исключают телефонные номера из evidence даже при явном запросе контакта. Подходящие legacy-документы без новых retrieval metadata остаются доступными. При недостаточном evidence отвечать отказом/уточнением, а не расширять поиск нерелевантными документами.

### Logging and export

Использовать стандартный уровень из environment с безопасным default `ERROR`; `DEBUG` разрешает диагностические поля без секретов и текстов токенов. Export command должен собирать только разрешённые gateway logs, архивировать с ограниченными правами и сообщать путь/ошибку. Структурированные события сохраняют request id, provider и fallback status.

## Risks / Trade-offs

- [Risk] Fallback может скрыть реальную проблему Selora → Mitigation: audit event, metric и уведомление пользователя.
- [Risk] Большое evidence повышает prompt size → Mitigation: bounded positive integer и общий character limit.
- [Risk] Debug logs могут раскрыть данные → Mitigation: redaction, allowlist полей и запрет secret values.
- [Risk] TTL удалит незавершённый feedback → Mitigation: понятное сообщение и безопасный повторный flow.

## Migration Plan

Сначала добавить settings и контракты обратимо, затем подключить provider chain и Telegram states. Старые локальные model settings сохраняются как fallback/default до заполнения Selora configuration. После focused checks включить `ERROR` в production и отдельно проверить `DEBUG` в disposable environment.
