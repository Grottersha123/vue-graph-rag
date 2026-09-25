from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Protocol

from mvp_v2.application.dto.chat import ChatAnswer, ChatRequest
from mvp_v2.application.dto.retrieval import EmbeddingPurpose, Evidence, SearchRequest
from mvp_v2.application.dump_lifecycle.manifests import DatabaseManifest
from mvp_v2.application.ingestion_contracts import ModelAuthorization
from mvp_v2.application.rag.agent.prompts import PromptTemplate
from mvp_v2.backend.audit import (
    BackendEvent,
    ErrorEvent,
    FeedbackEvent,
    GatewayAudit,
    PromptTraceContext,
    event_envelope,
    exception_diagnostics,
)
from mvp_v2.backend.contracts import (
    FeedbackRequest,
    FeedbackResponse,
    MessageRequest,
    MessageResponse,
    ProfileSelectionResponse,
    ProfilesResponse,
    ProfileSummary,
    SourceSummary,
)
from mvp_v2.backend.profiles import (
    ProfileRegistry,
    ProfileStore,
    ProfileStoreUnavailableError,
)


class ChatAnswerer(Protocol):
    def answer(self, request: ChatRequest) -> ChatAnswer: ...


class TraceBinder(Protocol):
    def bind(self, context: PromptTraceContext) -> AbstractContextManager[None]: ...


class NullTraceBinder:
    def bind(self, context: PromptTraceContext) -> AbstractContextManager[None]:
        return nullcontext()


class GatewayForbiddenError(PermissionError):
    pass


class GatewayInvalidError(ValueError):
    pass


class GatewayNotFoundError(LookupError):
    pass


class GatewayOverloadedError(RuntimeError):
    pass


class GatewayUnavailableError(RuntimeError):
    pass


class CapacityLimiter:
    def __init__(self, total_limit: int, model_limits: Mapping[str, int]) -> None:
        if (
            total_limit < 1
            or not model_limits
            or any(value < 1 for value in model_limits.values())
        ):
            raise ValueError("capacity limits must be positive")
        self._total = threading.BoundedSemaphore(total_limit)
        self._models = {
            model: threading.BoundedSemaphore(limit)
            for model, limit in model_limits.items()
        }

    @contextmanager
    def acquire(self, model_id: str) -> Iterator[None]:
        model = self._models.get(model_id)
        if model is None:
            raise ValueError("model concurrency limit is not configured")
        if not self._total.acquire(blocking=False):
            raise GatewayOverloadedError("gateway capacity is exhausted")
        acquired_model = model.acquire(blocking=False)
        if not acquired_model:
            self._total.release()
            raise GatewayOverloadedError("model capacity is exhausted")
        try:
            yield
        finally:
            model.release()
            self._total.release()


class GatewayChatService:
    def __init__(
        self,
        *,
        chats: Mapping[str, ChatAnswerer],
        manifest: DatabaseManifest,
        profiles: ProfileRegistry,
        profile_store: ProfileStore,
        audit: GatewayAudit,
        trace_binder: TraceBinder,
        capacity: CapacityLimiter,
        allowed_user_ids: frozenset[int],
        chat_model: str,
        chat_model_version: str,
        evidence_limit: int,
        minimum_score: float,
    ) -> None:
        if frozenset(chats) != frozenset(item.name for item in profiles.all()):
            raise ValueError("chat services must match prompt profiles")
        self._chats = chats
        self._manifest = manifest
        self._profiles = profiles
        self._profile_store = profile_store
        self._audit = audit
        self._trace_binder = trace_binder
        self._capacity = capacity
        self._allowed_user_ids = allowed_user_ids
        self._chat_model = chat_model
        self._chat_model_version = chat_model_version
        self._evidence_limit = evidence_limit
        self._minimum_score = minimum_score

    def record_independent_error(
        self,
        operation: str,
        error_code: str,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
        exception: BaseException | None = None,
    ) -> None:
        error, formatted_traceback = exception_diagnostics(exception)
        self._audit.error_writer.append(
            ErrorEvent.model_validate(
                {
                    **event_envelope(
                        component="backend",
                        event_type="error",
                        request_id=request_id,
                        conversation_id=conversation_id,
                        status="error",
                    ),
                    "operation": operation,
                    "error_code": error_code,
                    "message": "The operation could not be completed.",
                    "error": error,
                    "traceback": formatted_traceback,
                }
            )
        )

    def profiles(self) -> ProfilesResponse:
        return ProfilesResponse(
            profiles=tuple(
                ProfileSummary(
                    profile_id=item.name,
                    display_name=item.name,
                    is_default=item.name == self._profiles.default_id,
                )
                for item in self._profiles.all()
            ),
            default_profile_id=self._profiles.default_id,
        )

    def set_profile(
        self, telegram_user_id: int, profile_id: str
    ) -> ProfileSelectionResponse:
        try:
            self._authorize(telegram_user_id)
        except GatewayForbiddenError:
            self.record_independent_error("profile_update", "forbidden")
            raise
        try:
            self._profiles.require(profile_id)
        except ValueError as error:
            self.record_independent_error(
                "profile_update", "invalid_profile", exception=error
            )
            raise GatewayInvalidError("unknown prompt profile") from error
        try:
            self._profile_store.write(telegram_user_id, profile_id)
        except ProfileStoreUnavailableError as error:
            self.record_independent_error(
                "profile_update",
                "service_unavailable",
                exception=error,
            )
            raise GatewayUnavailableError("profile storage is unavailable") from error
        return ProfileSelectionResponse(
            telegram_user_id=telegram_user_id, profile_id=profile_id
        )

    def handle(self, request: MessageRequest) -> MessageResponse:
        started_ns = time.monotonic_ns()
        try:
            self._authorize(request.telegram_user_id)
            template = self._resolve_profile(request)
        except GatewayForbiddenError:
            self._record_error(request, "message", "forbidden")
            self._record_backend(request, started_ns, "rejected")
            raise
        except ProfileStoreUnavailableError as error:
            self._record_error(
                request, "profile_resolution", "service_unavailable", error
            )
            self._record_backend(request, started_ns, "error")
            raise GatewayUnavailableError("profile storage is unavailable") from error
        except ValueError as error:
            self._record_error(request, "profile_resolution", "invalid_profile", error)
            self._record_backend(request, started_ns, "rejected")
            raise GatewayInvalidError("unknown prompt profile") from error
        try:
            with self._capacity.acquire(self._chat_model):
                answer = self._answer(request, template)
        except GatewayOverloadedError:
            self._record_backend(request, started_ns, "overloaded", profile=template)
            raise
        except Exception as error:
            self._record_error(request, "message", "service_unavailable", error)
            self._record_backend(request, started_ns, "error", profile=template)
            raise GatewayUnavailableError("chat service is unavailable") from error
        response = MessageResponse(
            request_id=request.request_id,
            answer=answer.answer,
            sources=tuple(self._source(item) for item in answer.evidence),
            profile_id=template.name,
            prompt_version=template.name,
            duration_ms=self._duration_ms(started_ns),
        )
        self._record_backend(
            request,
            started_ns,
            "success",
            profile=template,
            answer=answer,
        )
        self._audit.completed.add(
            request.request_id,
            request.telegram_user_id,
            request.conversation_id,
        )
        return response

    def accept_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        try:
            self._authorize(request.telegram_user_id)
        except GatewayForbiddenError:
            self.record_independent_error(
                "feedback",
                "forbidden",
                request_id=request.request_id,
                conversation_id=request.conversation_id,
            )
            raise
        if not self._audit.completed.claim(
            request.request_id,
            request.telegram_user_id,
            request.conversation_id,
        ):
            self.record_independent_error(
                "feedback",
                "unknown_request",
                request_id=request.request_id,
                conversation_id=request.conversation_id,
            )
            raise GatewayNotFoundError("request_id is unknown")
        try:
            self._audit.feedback_writer.append(
                FeedbackEvent.model_validate(
                    {
                        **event_envelope(
                            component="backend",
                            event_type="feedback",
                            request_id=request.request_id,
                            conversation_id=request.conversation_id,
                            status="accepted",
                        ),
                        "telegram_user_id": request.telegram_user_id,
                        "rating": request.rating,
                        "comment": request.comment,
                    }
                )
            )
        except Exception:
            self._audit.completed.add(
                request.request_id,
                request.telegram_user_id,
                request.conversation_id,
            )
            raise
        return FeedbackResponse(accepted=True, request_id=request.request_id)

    def _resolve_profile(self, request: MessageRequest) -> PromptTemplate:
        return self._profiles.resolve(
            request.profile_id,
            request.telegram_user_id,
            self._profile_store,
        )

    def _answer(self, request: MessageRequest, template: PromptTemplate) -> ChatAnswer:
        search_request = SearchRequest(
            question=request.question,
            space=self._manifest.embedding_space,
            purposes=(
                EmbeddingPurpose.CHUNK_RETRIEVAL,
                EmbeddingPurpose.ASSET_DESCRIPTION_RETRIEVAL,
            ),
            limit=self._evidence_limit,
            authorization=ModelAuthorization(allow=True, operation="embedding"),
            minimum_score=self._minimum_score,
        )
        trace = PromptTraceContext(
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            profile_id=template.name,
            prompt_version=template.name,
            database_name=self._manifest.database_name,
            database_build_id=self._manifest.build_id,
            graph_schema_version=self._manifest.graph_schema_version,
        )
        with self._trace_binder.bind(trace):
            return self._chats[template.name].answer(
                ChatRequest(
                    question=request.question,
                    search=search_request,
                    model=self._chat_model,
                    model_version=self._chat_model_version,
                    authorization=ModelAuthorization(
                        allow=True, operation="generation"
                    ),
                )
            )

    def _record_backend(
        self,
        request: MessageRequest,
        started_ns: int,
        status: str,
        *,
        profile: PromptTemplate | None = None,
        answer: ChatAnswer | None = None,
    ) -> None:
        self._audit.backend_writer.append(
            BackendEvent.model_validate(
                {
                    **event_envelope(
                        component="backend",
                        event_type="message",
                        request_id=request.request_id,
                        conversation_id=request.conversation_id,
                        status=status,
                    ),
                    "telegram_user_id": request.telegram_user_id,
                    "question": request.question,
                    "answer": None if answer is None else answer.answer,
                    "profile_id": None if profile is None else profile.name,
                    "prompt_version": None if profile is None else profile.name,
                    "database_name": self._manifest.database_name,
                    "database_build_id": self._manifest.build_id,
                    "graph_schema_version": self._manifest.graph_schema_version,
                    "model_id": f"{self._chat_model}:{self._chat_model_version}",
                    "latency_ms": self._duration_ms(started_ns),
                    "retrieval_count": 0 if answer is None else len(answer.evidence),
                    "evidence_ids": ()
                    if answer is None
                    else tuple(item.embedding_id for item in answer.evidence),
                }
            )
        )

    def _record_error(
        self,
        request: MessageRequest,
        operation: str,
        error_code: str,
        exception: BaseException | None = None,
    ) -> None:
        error, formatted_traceback = exception_diagnostics(exception)
        self._audit.error_writer.append(
            ErrorEvent.model_validate(
                {
                    **event_envelope(
                        component="backend",
                        event_type="error",
                        request_id=request.request_id,
                        conversation_id=request.conversation_id,
                        status="error",
                    ),
                    "operation": operation,
                    "error_code": error_code,
                    "message": "The operation could not be completed.",
                    "error": error,
                    "traceback": formatted_traceback,
                }
            )
        )

    def _authorize(self, telegram_user_id: int) -> None:
        if telegram_user_id not in self._allowed_user_ids:
            raise GatewayForbiddenError("user is not allowed")

    @staticmethod
    def _duration_ms(started_ns: int) -> int:
        return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)

    @staticmethod
    def _source(evidence: Evidence) -> SourceSummary:
        return SourceSummary(
            evidence_id=evidence.embedding_id,
            document_id=evidence.document_id,
            title=evidence.document_title,
            source_url=evidence.source_url,
            local_path=evidence.local_path,
            score=evidence.score,
        )
