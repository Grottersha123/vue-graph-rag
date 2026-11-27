from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from vue_rag.backend.audit import AuditWriteError
from vue_rag.backend.config import GatewaySettings
from vue_rag.backend.contracts import (
    ErrorHttpResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthHttpResponse,
    MessageRequest,
    MessageResponse,
    ProfileSelectionRequest,
    ProfileSelectionResponse,
    ProfilesResponse,
)
from vue_rag.backend.runtime import GatewayRuntime, build_runtime
from vue_rag.backend.service import (
    GatewayChatService,
    GatewayForbiddenError,
    GatewayInvalidError,
    GatewayNotFoundError,
    GatewayOverloadedError,
    GatewayUnavailableError,
)


class GatewayServiceAuthError(RuntimeError):
    pass


class Readiness(Protocol):
    def check(self) -> HealthHttpResponse: ...


RuntimeFactory = Callable[[GatewaySettings], GatewayRuntime]


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    payload = ErrorHttpResponse.model_validate({"error": code, "detail": detail})
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(mode="json")
    )


def create_application(
    *,
    gateway: GatewayChatService | None = None,
    readiness: Readiness | None = None,
    settings: GatewaySettings | None = None,
    api_token: str | None = None,
    runtime_factory: RuntimeFactory = build_runtime,
) -> FastAPI:
    if gateway is not None and api_token is None:
        raise ValueError("api_token is required with an injected gateway")
    runtime: GatewayRuntime | None = None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal runtime, gateway, readiness, api_token
        if gateway is None or readiness is None:
            resolved = settings or GatewaySettings.from_environment()
            api_token = resolved.backend_token
            runtime = runtime_factory(resolved)
            gateway = runtime.gateway
            readiness = runtime.readiness
        yield
        if runtime is not None:
            runtime.close()

    application = FastAPI(title="MVP v2 local RAG gateway", lifespan=lifespan)

    def services() -> tuple[GatewayChatService, Readiness]:
        if gateway is None or readiness is None:
            raise RuntimeError("gateway runtime is not initialized")
        return gateway, readiness

    def record_api_error(
        operation: str,
        error_code: str,
        body: object = None,
    ) -> None:
        if gateway is None:
            return
        request_id: str | None = None
        conversation_id: str | None = None
        if isinstance(body, Mapping):
            raw_request_id = body.get("request_id")
            raw_conversation_id = body.get("conversation_id")
            request_id = raw_request_id if isinstance(raw_request_id, str) else None
            conversation_id = (
                raw_conversation_id if isinstance(raw_conversation_id, str) else None
            )
        try:
            gateway.record_independent_error(
                operation,
                error_code,
                request_id=request_id,
                conversation_id=conversation_id,
            )
        except AuditWriteError:
            pass

    def service_auth(request: Request) -> None:
        if api_token is None:
            return
        authorization = request.headers.get("Authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not secrets.compare_digest(credential, api_token)
        ):
            raise GatewayServiceAuthError("service authentication failed")

    @application.get("/health", response_model=HealthHttpResponse)
    def health() -> Any:
        _, probe = services()
        result = probe.check()
        if result.status != "ready":
            return JSONResponse(status_code=503, content=result.model_dump(mode="json"))
        return result

    @application.get(
        "/v1/profiles",
        response_model=ProfilesResponse,
        dependencies=[Depends(service_auth)],
    )
    def profiles() -> ProfilesResponse:
        service, _ = services()
        return service.profiles()

    @application.post(
        "/v1/messages",
        response_model=MessageResponse,
        dependencies=[Depends(service_auth)],
    )
    def message(payload: MessageRequest) -> MessageResponse:
        service, _ = services()
        return service.handle(payload)

    @application.put(
        "/v1/users/{telegram_user_id}/profile",
        response_model=ProfileSelectionResponse,
        dependencies=[Depends(service_auth)],
    )
    def select_profile(
        telegram_user_id: int, payload: ProfileSelectionRequest
    ) -> ProfileSelectionResponse:
        service, _ = services()
        return service.set_profile(telegram_user_id, payload.profile_id)

    @application.post(
        "/v1/feedback",
        response_model=FeedbackResponse,
        dependencies=[Depends(service_auth)],
    )
    def feedback(payload: FeedbackRequest) -> FeedbackResponse:
        service, _ = services()
        return service.accept_feedback(payload)

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        record_api_error("request_validation", "invalid_request", error.body)
        return _error(422, "invalid_request", "Request validation failed.")

    @application.exception_handler(GatewayInvalidError)
    async def invalid_error(
        request: Request, error: GatewayInvalidError
    ) -> JSONResponse:
        return _error(422, "invalid_request", "Request validation failed.")

    @application.exception_handler(GatewayForbiddenError)
    async def forbidden_error(
        request: Request, error: GatewayForbiddenError
    ) -> JSONResponse:
        return _error(403, "forbidden", "Access is denied.")

    @application.exception_handler(GatewayNotFoundError)
    async def not_found_error(
        request: Request, error: GatewayNotFoundError
    ) -> JSONResponse:
        return _error(404, "not_found", "The referenced request was not found.")

    @application.exception_handler(GatewayOverloadedError)
    async def overloaded_error(
        request: Request, error: GatewayOverloadedError
    ) -> JSONResponse:
        return _error(503, "overloaded", "The service is temporarily overloaded.")

    @application.exception_handler(GatewayUnavailableError)
    async def unavailable_error(
        request: Request, error: GatewayUnavailableError
    ) -> JSONResponse:
        return _error(503, "service_unavailable", "The service is unavailable.")

    @application.exception_handler(GatewayServiceAuthError)
    async def service_auth_error(
        request: Request, error: GatewayServiceAuthError
    ) -> JSONResponse:
        record_api_error("service_auth", "unauthorized")
        return _error(401, "unauthorized", "Service authentication is required.")

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        record_api_error("http_request", "internal_error")
        return _error(500, "internal_error", "An internal error occurred.")

    return application


app = create_application()
