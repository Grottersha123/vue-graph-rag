# Onboarding Classifier — мини-классификатор диалогов

Определяет по диалогу, относится ли он к процессу онбординга ученика VEU.

Модель: gemma-4-e4b (или qwen3.6) через LM Studio на 192.168.178.170:1234.
Всё в датаклассах: `Dialog`, `DialogTurn`, `OnboardingSignal`, `Classification`,
классификатор — `OnboardingClassifier`.

## Промт

Дизайн в `classifier.py::SYSTEM_PROMPT`:

- позитивные и негативные примеры этапов (что онбординг, а что до/после него) —
  границы взяты из «Main competents — по процессам» (процессы 1 и 3+ — НЕ онбординг);
- жёсткий формат ответа: только JSON `{stage, confidence, signals[]}`;
- требование цитат-маркеров из диалога (signals) — чтобы ответ можно было проверить глазами;
- правило для пограничных случаев (LinkedIn-регистрация = onboarding, CV = нет).

Парсинг устойчив к мусору: `_JSON_RE` достаёт JSON из любого ответа,
при ошибке — `Classification(NOT_ONBOARDING, LOW, ...)` без падения.

## Запуск

```bash
# LM Studio должен быть поднят; модель загружена с контекстом >= 8k
.venv/bin/python classifier.py examples/sample-dialogues.json --model gemma-4-e4b
```

## Формат входа

```json
[{"dialog_id": "dlg-001",
  "turns": [{"role": "coordinator", "text": "..."}, {"role": "student", "text": "..."}]}]
```

## Дальше (эксперименты)

- [ ] Прогнать sample и проверить глазами signals: честные ли цитаты.
- [ ] Собрать 10-20 реальных диалогов из чатов, разметить вручную.
- [ ] Посчитать accuracy/precision-recall на размеченном, сравнить gemma vs qwen3.6.
- [ ] Если модель путает границы: уточнять SYSTEM_PROMPT, не код.
