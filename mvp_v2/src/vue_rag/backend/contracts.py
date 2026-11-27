from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from vue_rag.domain.base import ContractModel

TelegramUserId = Annotated[int, Field(strict=True, ge=1)]
TelegramChatId = Annotated[int, Field(strict=True)]
TelegramMessageId = Annotated[int, Field(strict=True, ge=1)]
RequestId = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
]
ConversationId = Annotated[str, Field(min_length=1, max_length=128)]
Question = Annotated[str, Field(min_length=1, max_length=2_000)]
ProfileId = Annotated[
    str, Field(min_length=1, max_length=48, pattern=r"^[A-Za-z0-9._-]+$")
]


class MessageRequest(ContractModel):
    request_id: RequestId
    telegram_user_id: TelegramUserId
    chat_id: TelegramChatId
    source_message_id: TelegramMessageId
    conversation_id: ConversationId
    question: Question
    profile_id: ProfileId | None = None

    @model_validator(mode="after")
    def conversation_matches_chat(self) -> MessageRequest:
        if self.conversation_id != str(self.chat_id):
            raise ValueError("conversation_id must equal chat_id")
        return self


class SourceSummary(ContractModel):
    evidence_id: str
    document_id: str
    title: str
    source_url: str
    local_path: str
    score: float


class MessageResponse(ContractModel):
    request_id: RequestId
    answer: str
    sources: tuple[SourceSummary, ...]
    profile_id: ProfileId
    prompt_version: str
    duration_ms: Annotated[int, Field(strict=True, ge=0)]


class ProfileSummary(ContractModel):
    profile_id: ProfileId
    display_name: str
    is_default: bool


class ProfilesResponse(ContractModel):
    profiles: tuple[ProfileSummary, ...]
    default_profile_id: ProfileId


class ProfileSelectionRequest(ContractModel):
    profile_id: ProfileId


class ProfileSelectionResponse(ContractModel):
    telegram_user_id: TelegramUserId
    profile_id: ProfileId


class FeedbackRequest(ContractModel):
    request_id: RequestId
    telegram_user_id: TelegramUserId
    conversation_id: ConversationId
    rating: Annotated[int, Field(strict=True, ge=1, le=5)]
    comment: Annotated[str, Field(max_length=2_000)] | None = None


class FeedbackResponse(ContractModel):
    accepted: Literal[True]
    request_id: RequestId


class HealthHttpResponse(ContractModel):
    status: Literal["ready", "not_ready"]
    database: bool
    ollama: bool
    redis: bool
    audit: bool


class ErrorHttpResponse(ContractModel):
    error: Literal[
        "invalid_request",
        "unauthorized",
        "forbidden",
        "not_found",
        "overloaded",
        "service_unavailable",
        "internal_error",
    ]
    detail: str
