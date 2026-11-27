"""Классификатор диалога: относится ли он к процессу онбординга VEU.

Запуск (когда LM Studio поднят):
    .venv/bin/python classifier.py examples/sample-dialogues.json

Модель: gemma-4-e4b (делегация через LM Studio) или qwen3.6 — задать флагом --model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# ---------- Домен ----------

class Stage(str, Enum):
    ONBOARDING = "onboarding"          # онбординг ученика
    NOT_ONBOARDING = "not_onboarding"  # другой процесс или болтовня


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class OnboardingSignal:
    """Один признак онбординга, найденный в диалоге."""
    marker: str          # фраза/факт из диалога, который сработал
    reason: str          # почему это указывает на онбординг


@dataclass(frozen=True)
class Classification:
    """Итог классификации одного диалога."""
    stage: Stage
    confidence: Confidence
    signals: tuple[OnboardingSignal, ...] = ()
    raw_reply: str = ""          # сырой ответ модели (для отладки)
    model: str = ""
    dialog_id: str = ""

    @property
    def is_onboarding(self) -> bool:
        return self.stage is Stage.ONBOARDING


@dataclass
class DialogTurn:
    """Сообщение в диалоге."""
    role: str            # "user" | "assistant" | "mentor" | ...
    text: str


@dataclass
class Dialog:
    """Диалог для классификации."""
    dialog_id: str
    turns: list[DialogTurn] = field(default_factory=list)

    def as_text(self, max_chars: int = 3000) -> str:
        lines = [f"{t.role}: {t.text}" for t in self.turns]
        joined = "\n".join(lines)
        return joined[:max_chars]


# ---------- Промт ----------

SYSTEM_PROMPT = """Ты — строгий классификатор диалогов в компании VEU.

VEU помогает студентам устроиться на работу. ПРОЦЕСС ОНБОРДИНГА — это первые шаги
нового ученика ПОСЛЕ оплаты, до выхода на рынок труда:

- создание личной папки и рабочих чатов (Google Drive, Telegram),
- сбор данных ученика: образование, уровень английского, описание опыта, самопрезентация,
- заведение ученика в NocoDB (clients, platform_accounts, chat ID),
- настройка инструментов: прокси, Dolphin Browser, телефонный номер, новая почта,
- регистрация и оформление LinkedIn,
- выдача инструкций и проверка их выполнения,
- доступ к обучающим материалам (чат по стеку, поддержка, менторы).

ЭТО НЕ ОНБОРДИНГ:
- маркетинг, продажа, договор, оплата (это этап ДО онбординга),
- создание CV и отклики на вакансии, HH, интервью, офферы (это этапы ПОСЛЕ),
- технический менторинг по стеку (React/Python/Go/Java),
- внутренние дела компании: платежи, бухгалтерия, хостинг, парсеры,
- обычная болтовня или вопросы не по процессу.

ПРАВИЛА ОТВЕТА:
1. Ответ — ТОЛЬКО валидный JSON, без markdown и пояснений.
2. Формат:
{"stage": "onboarding" | "not_onboarding", "confidence": "high" | "medium" | "low", "signals": [{"marker": "...", "reason": "..."}]}
3. В signals приведи 1-3 конкретные фразы ИЗ диалога (marker — почти дословная цитата)
   и краткое объяснение, почему они указывают на твой вывод.
4. Если диалог пограничный (например, регистрация LinkedIn уже идёт, но разговор
   ушёл в CV) — выбери более ранний этап, то есть onboarding, и поставь confidence: medium.
5. Если релевантных фраз нет вообще — not_onboarding, confidence: high, пустой signals.
"""


def build_user_prompt(dialog: Dialog) -> str:
    """Промт пользователя: диалог + жёсткое требование формата."""
    return (
        "Классифицируй диалог ниже.\n\n"
        "ДИАЛОГ:\n"
        f"{dialog.as_text()}\n\n"
        "Ответь только JSON по формату из правил."
    )


# ---------- Парсинг ответа ----------

_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_reply(raw: str, dialog_id: str, model: str) -> Classification:
    """Достаёт JSON из ответа модели, валидирует в датаклассы."""
    match = _JSON_RE.search(raw)
    if not match:
        return Classification(Stage.NOT_ONBOARDING, Confidence.LOW,
                              (), raw, model, dialog_id)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Classification(Stage.NOT_ONBOARDING, Confidence.LOW,
                              (), raw, model, dialog_id)

    stage_raw = str(data.get("stage", "")).strip().lower()
    stage = Stage.ONBOARDING if stage_raw == "onboarding" else Stage.NOT_ONBOARDING

    conf_raw = str(data.get("confidence", "low")).strip().lower()
    confidence = Confidence(conf_raw) if conf_raw in Confidence._value2member_map_ else Confidence.LOW

    signals = tuple(
        OnboardingSignal(marker=str(s.get("marker", ""))[:200],
                         reason=str(s.get("reason", ""))[:300])
        for s in data.get("signals", []) if isinstance(s, dict)
    )
    return Classification(stage, confidence, signals, raw, model, dialog_id)


# ---------- Классификатор ----------

@dataclass
class OnboardingClassifier:
    """Обёртка над LM Studio (OpenAI-совместимый API).

    endpoint — базовый URL, например http://192.168.178.170:1234/v1
    model    — id модели в LM Studio (gemma-4-e4b, qwen3.6 и т.п.)
    """
    endpoint: str = "http://192.168.178.170:1234/v1"
    model: str = "gemma-4-e4b"
    temperature: float = 0.0
    max_tokens: int = 300
    timeout_s: int = 60

    def _chat(self, user_prompt: str) -> str:
        import urllib.request
        payload = json.dumps({
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/chat/completions", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]

    def classify(self, dialog: Dialog) -> Classification:
        raw = self._chat(build_user_prompt(dialog))
        return parse_reply(raw, dialog.dialog_id, self.model)


# ---------- Загрузка диалогов ----------

def load_dialogs(path: str) -> list[Dialog]:
    data = json.load(open(path, encoding="utf-8"))
    dialogs = []
    for item in data:
        turns = [DialogTurn(role=t.get("role", "user"), text=t["text"])
                 for t in item.get("turns", [])]
        dialogs.append(Dialog(dialog_id=item.get("dialog_id", "?"), turns=turns))
    return dialogs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="JSON со списком диалогов")
    ap.add_argument("--model", default="gemma-4-e4b")
    ap.add_argument("--endpoint", default="http://192.168.178.170:1234/v1")
    args = ap.parse_args()

    clf = OnboardingClassifier(endpoint=args.endpoint, model=args.model)
    for dialog in load_dialogs(args.input):
        result = clf.classify(dialog)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
