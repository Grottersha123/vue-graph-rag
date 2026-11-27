from __future__ import annotations

from vue_rag.ports.models import (
    GenerationProvider,
    GenerationResult,
    ProviderGenerationError,
    TextGenerationModel,
)


class NamedGenerationProvider:
    def __init__(self, model: TextGenerationModel, provider: str) -> None:
        if not provider.strip():
            raise ValueError("provider identity must not be empty")
        self._model = model
        self._provider = provider

    def generate(self, *, prompt: str, model: str) -> GenerationResult:
        return GenerationResult(
            text=self._model.generate(prompt=prompt, model=model),
            provider=self._provider,
        )


class FallbackGenerationProvider:
    def __init__(
        self, primary: GenerationProvider, fallback: GenerationProvider
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def generate(self, *, prompt: str, model: str) -> GenerationResult:
        try:
            return self._primary.generate(prompt=prompt, model=model)
        except ProviderGenerationError as error:
            result = self._fallback.generate(prompt=prompt, model=model)
            return GenerationResult(
                text=result.text,
                provider=result.provider,
                fallback_used=True,
                fallback_reason=error.reason,
                request_id=result.request_id,
            )
