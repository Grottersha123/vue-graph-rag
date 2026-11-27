# Token-efficient OpenSpec workflow

## Назначение

Это руководство описывает экономный по токенам процесс работы с OpenSpec в
`mvp_v2`. Цель — сохранить согласование требований и надёжную проверку, но не
загружать AI-агента всей историей проекта, архивом изменений и повторяющимися
логами.

Руководство основано на официальной документации OpenSpec и опыте реализации
локального FastAPI/Telegram gateway.

## Официальные источники

- [OpenSpec](https://openspec.dev)
- [Quickstart](https://openspec.dev/docs/quickstart)
- [Project configuration](https://openspec.dev/docs/project-config)
- [Profiles](https://openspec.dev/docs/profiles)
- [CLI reference](https://openspec.dev/docs/cli)
- [Official GitHub repository](https://github.com/Fission-AI/OpenSpec)

Локально проверенная версия CLI на момент подготовки документа: `1.13.1`.

## Краткий принцип

Используйте три отдельные сессии:

1. **Planning** — explore, proposal, specs, design и tasks. Код не изменяется.
2. **Apply** — новая чистая сессия реализует принятые tasks.
3. **Verify and archive** — короткая сессия проверяет результат, создаёт commit и
   архивирует change.

OpenSpec хранит прогресс в `tasks.md`. История чата не должна быть единственным
источником состояния.

## Когда нужен OpenSpec

### OpenSpec не обязателен

Можно выполнить обычный focused commit, если изменение не меняет наблюдаемое
поведение:

- исправление опечатки;
- formatting;
- комментарий;
- внутренний рефакторинг без изменения контракта;
- механическое обновление документации.

### Нужен небольшой OpenSpec change

Создавайте change на 3–10 задач, если меняется поведение:

- новый endpoint или command;
- validation rule;
- configuration option;
- error contract;
- audit event;
- deployment operation.

### Сначала нужен explore

Начинайте с explore, если неизвестны важные решения:

- новая архитектурная граница;
- storage или migration strategy;
- новый provider;
- access control;
- изменение immutable data contract;
- несколько конкурирующих вариантов реализации.

Explore исследует и уточняет задачу, но не пишет production code.

## Размер change

Практическая рекомендация для этого проекта:

- 3–10 tasks — оптимальный размер;
- 10–15 tasks — допустимо;
- больше 15 tasks — проверить возможность разделения;
- больше 10 spec deltas — почти всегда разделять change.

Один change должен давать один независимо проверяемый результат.

Вместо одного большого change для API, Telegram, audit и containers лучше
создать последовательность:

1. `add-local-rag-api`;
2. `add-telegram-adapter`;
3. `add-gateway-audit`;
4. `containerize-local-gateway`.

После archive каждого change его requirements становятся baseline specs для
следующего.

# Сессия 1: planning

## 1. Сформулировать контракт

До запуска агента зафиксируйте:

- пользовательскую ценность;
- пользователя результата;
- observable behavior;
- in scope;
- out of scope;
- запрещённые изменения;
- ожидаемые артефакты;
- acceptance criteria;
- runtime dependencies и доступность внешних сервисов.

## 2. Создать change

Используйте имя в `kebab-case`:

```bash
openspec new change <change-name> \
  --description "<что меняется>" \
  --goal "<какая ценность>"
```

Проверить состояние артефактов:

```bash
openspec status --change <change-name>
```

## 3. Подготовить artifacts одним проходом

Если задача ясна, не тратьте отдельную сессию на explore. Запросите все
planning artifacts сразу:

```text
Подготовь OpenSpec change <change-name>.

Ценность:
<одно предложение>

Пользователь:
<кто получает результат>

In scope:
- ...

Out of scope:
- ...

Не изменять:
- ...

Acceptance:
- ...

Сначала создай proposal, specs, design и tasks.
Код не меняй.
Proposal до 400 слов.
Не более 15 outcome-level tasks.
```

## 4. Читать artifacts в правильном порядке

Проверяйте только:

1. `proposal.md` — правильная ли проблема и размер change;
2. `specs/` — устраивает ли observable behavior;
3. `tasks.md` — покрывают ли tasks specs без лишней работы;
4. `design.md` — только существенные решения и trade-offs.

По официальному Quickstart, specs — самая ценная часть review.

## 5. Проверить planning

```bash
openspec validate <change-name> --strict
```

После принятия artifacts завершите planning-сессию. Не начинайте реализацию в
том же переполненном context window.

# Сессия 2: apply

## 1. Начать новую сессию

Официальный OpenSpec Quickstart рекомендует чистый context window перед apply.
Используйте запрос:

```text
Примени OpenSpec change <change-name>.
Работай по tasks.md.
Читай только active change, затронутые baseline specs и непосредственно
связанные source/tests.
Запускай focused checks после каждой фазы.
Не отмечай task без фактического подтверждения.
```

## 2. Читать минимальный контекст

Нужно читать:

- repository instructions;
- текущий change;
- непосредственно затронутые baseline specs;
- соседний production code;
- связанные tests.

Не нужно читать:

- весь `openspec/changes/archive/`;
- все baseline specs;
- полный Git history;
- несвязанные notebooks;
- другие проекты monorepo;
- старые runtime logs без нужного request ID.

## 3. Реализовывать фазами

Для каждой фазы:

1. реализовать минимальный связный результат;
2. выполнить focused tests;
3. выполнить Ruff/mypy только для затронутых Python-файлов;
4. обновить только подтверждённые checkboxes;
5. создать phase commit после принятия результата.

Не запускайте полный regression suite после каждого небольшого edit.

## 4. Продолжение в новой сессии

Если context заканчивается, не сохраняйте длинный пересказ в чате. Начните новую
сессию:

```text
Продолжи применение <change-name> с первой незакрытой задачи в tasks.md.
```

Checkboxes являются источником прогресса. Blockers должны быть записаны в
`tasks.md`, design или краткую project memory, а не существовать только в chat.

# Проверки Python в mvp_v2

Никогда не используйте host-owned `mvp_v2/.venv`.

Каждая Python-команда должна явно использовать внешний environment:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run <command>
```

До первой project-команды в новой сессии:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run python -c   'import sys; assert sys.executable.startswith("/tmp/vue-graph-rag-mvp-v2/.venv/")'
```

Экономный порядок проверок:

1. focused tests изменённого компонента;
2. focused Ruff;
3. focused mypy;
4. один relevant suite после фазы;
5. один regression run перед commit.

Не добавляйте требование «весь repository pytest обязан быть зелёным», если в
baseline уже есть подтверждённый несвязанный stale test. Зафиксируйте точный
regression scope до реализации.

# Container development loop

## Изменён только backend

```bash
podman-compose build backend
podman-compose up -d --force-recreate backend
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project . python -m pytest tests/backend
```

Проверку authenticated message выполняйте отдельным bounded client request только
когда требуется end-to-end RAG evidence; она больше не встроена в Makefile.

## Изменён только Telegram adapter

```bash
podman-compose build telegram
podman-compose up -d --force-recreate telegram
```

## Изменены общие DTO или contracts

```bash
podman-compose build backend telegram
podman-compose up -d --force-recreate backend telegram
```

## Финальная runtime-проверка

Полный deployment выполняется один раз перед acceptance:

```bash
make clean
make deploy
make health
make diagnose-models
make export-logs
```

`make deploy` подтверждает readiness, но не запускает smoke, security или
authenticated RAG tests. Нужные acceptance checks выполняйте отдельными native
commands и сохраняйте только их краткий результат.

Не удаляйте volumes. `make clean` сохраняет images, named volumes, secrets,
`.env` и database artifacts.

# Runtime debugging без лишних токенов

Используйте correlation по одному request ID:

1. найти последний failed request;
2. прочитать его `ErrorEvent`;
3. извлечь только `error` и последний exception chain;
4. проверить связанный prompt trace;
5. не печатать prompts, questions, tokens и старые unrelated events.

Диагностический порядок:

1. `make health`;
2. `make diagnose-models`;
3. отдельный bounded request к проблемному endpoint;
4. `make export-logs`;
5. последний event из `errors.jsonl`;
6. только при необходимости `backend-container.log` или
   `telegram-container.log`.

Не выводите полный log volume или весь metadata snapshot `.venv`. Для protected
paths достаточно boolean/result summary.

# Сессия 3: verify, commit and archive

## 1. Проверить соответствие artifacts

Проверить:

- каждый requirement имеет implementation evidence;
- каждый checked task реально подтверждён;
- runtime-dependent tasks имеют runtime evidence;
- protected paths не изменены агентом;
- external blockers отмечены как `NOT VERIFIED`.

## 2. Выполнить финальные checks

```bash
openspec validate <change-name> --strict
git diff --check
```

Для большого monorepo сначала используйте targeted output:

```bash
git status --short -- <change-paths>
git diff --stat -- <change-paths>
git diff --check -- <change-paths>
```

Полный `git status` нужен один раз непосредственно перед commit.

## 3. Создать implementation commit

Commit должен содержать только change scope. Несвязанные staged/unstaged файлы
не должны попадать в него.

Пример:

```text
feat(mvp_v2): add <capability>
```

Git является отдельным concern: archive OpenSpec не создаёт Git commit.

## 4. Архивировать change

После всех checked tasks:

```bash
openspec archive <change-name> --yes
```

Archive:

- применяет delta specs к `openspec/specs/`;
- переносит change в dated archive folder;
- не удаляет историю change.

## 5. Использовать findings-only validation

Чтобы не печатать длинный успешный отчёт:

```bash
openspec validate --all --strict --report findings --no-interactive
openspec validate --archived --strict --report findings --no-interactive
```

После validation создайте отдельный archive commit:

```text
docs(openspec): archive <change-name>
```

# Рекомендуемый openspec/config.yaml

Официальная документация предупреждает: каждая строка `context` и rules попадает
в prompt агента. Поэтому configuration должна оставаться короткой и не
дублировать `AGENTS.md`.

Рекомендуемый вариант:

```yaml
schema: spec-driven

context: |
  Write artifacts in Russian.
  Keep OpenSpec headings and SHALL/MUST keywords in English.

rules:
  proposal:
    - Keep the proposal under 400 words and limit it to one cohesive outcome.
  design:
    - Record only decisions and trade-offs that are not obvious from the code.
  tasks:
    - Prefer 5-15 outcome-level tasks; every task must name one acceptance check.

operations:
  apply:
    guidance:
      - Read only the active change and directly affected specs and source files.
      - Run focused checks per phase and one regression run before commit.
  archive:
    guidance:
      - Report only validation findings and the archive result.
```

Не добавляйте сюда Python environment, security и repository rules, если они уже
есть в `AGENTS.md`.

# OpenSpec profile

Для обычной работы достаточно default `core` profile:

- explore;
- propose;
- apply;
- update;
- sync;
- archive.

Не включайте все optional workflows постоянно. `verify` полезен для high-risk
changes: security, migrations, destructive data operations и сложных runtime
contracts. Для небольшого change достаточно focused verification.

# Антипаттерны

Избегайте следующего:

- один change на несколько независимых capabilities;
- реализация до принятия specs;
- planning и apply в одном переполненном чате;
- чтение всего archive при каждом apply;
- повторный полный pytest после каждого edit;
- repeated full container rebuild во время локальной итерации;
- вывод полных logs, prompts, secrets или `.env`;
- автоматическое закрытие tasks только потому, что `/health` успешен;
- исправление несвязанных baseline failures внутри текущего change;
- commit всего dirty monorepo вместо точного path scope;
- ручное изменение baseline specs до archive без явной причины.

# Definition of done

Change готов к archive, когда:

- [ ] proposal отражает реализованный scope;
- [ ] specs описывают фактическое observable behavior;
- [ ] все tasks проверены и отмечены;
- [ ] focused tests прошли;
- [ ] regression scope прошёл или blocker явно записан;
- [ ] runtime checks выполнены для runtime-dependent behavior;
- [ ] OpenSpec strict validation прошёл;
- [ ] `git diff --check` прошёл;
- [ ] protected paths проверены;
- [ ] implementation commit создан;
- [ ] change архивирован;
- [ ] baseline specs провалидированы;
- [ ] archive commit создан.

# Минимальный ежедневный сценарий

## Planning

```text
Создай небольшой OpenSpec change <name>.
Сначала только proposal/specs/design/tasks.
Код не меняй.
```

```bash
openspec validate <name> --strict
```

## Apply в новом чате

```text
Примени <name> с первой незакрытой задачи.
Читай только напрямую связанные files/specs.
```

## Finish

```bash
openspec validate <name> --strict
git diff --check
openspec archive <name> --yes
openspec validate --all --strict --report findings --no-interactive
```

Главная экономия достигается не короткими ответами, а маленькими changes, чистой
implementation-сессией и targeted context.
