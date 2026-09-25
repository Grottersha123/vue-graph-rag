from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from mvp_v2.backend.contracts import (
    FeedbackRequest,
    FeedbackResponse,
    MessageRequest,
    MessageResponse,
    ProfileSelectionRequest,
    ProfileSelectionResponse,
    ProfilesResponse,
)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class TelegramGatewayError(RuntimeError):
    pass


class HttpBackendClient:
    def __init__(
        self, client: httpx.AsyncClient, backend_url: str, backend_token: str
    ) -> None:
        self._client = client
        self._backend_url = backend_url
        self._authorization = f"Bearer {backend_token}"

    async def send_message(self, request: MessageRequest) -> MessageResponse:
        response = await self._request(
            "POST", "/v1/messages", json=request.model_dump(mode="json")
        )
        return self._parse(response, MessageResponse)

    async def profiles(self) -> ProfilesResponse:
        response = await self._request("GET", "/v1/profiles")
        return self._parse(response, ProfilesResponse)

    async def select_profile(
        self, telegram_user_id: int, profile_id: str
    ) -> ProfileSelectionResponse:
        payload = ProfileSelectionRequest(profile_id=profile_id)
        response = await self._request(
            "PUT",
            f"/v1/users/{telegram_user_id}/profile",
            json=payload.model_dump(mode="json"),
        )
        return self._parse(response, ProfileSelectionResponse)

    async def submit_feedback(self, request: FeedbackRequest) -> FeedbackResponse:
        response = await self._request(
            "POST", "/v1/feedback", json=request.model_dump(mode="json")
        )
        return self._parse(response, FeedbackResponse)

    async def _request(
        self, method: str, path: str, *, json: dict[str, object] | None = None
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._backend_url}{path}",
                json=json,
                headers={"Authorization": self._authorization},
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            raise TelegramGatewayError("local backend request failed") from error

    @staticmethod
    def _parse(response: httpx.Response, contract: type[ResponseT]) -> ResponseT:
        try:
            return contract.model_validate_json(response.content)
        except (ValidationError, ValueError) as error:
            raise TelegramGatewayError("local backend response is invalid") from error
