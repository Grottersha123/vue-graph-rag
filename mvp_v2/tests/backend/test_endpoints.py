from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mvp_v2.backend.app import create_application
from mvp_v2.backend.audit import BackendEvent, ErrorEvent, FeedbackEvent
from mvp_v2.backend.service import CapacityLimiter
from tests.backend.helpers import FakeChat, ReadyProbe, gateway

API_TOKEN = "x" * 32
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def _message(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "request-1",
        "telegram_user_id": 42,
        "chat_id": -100,
        "source_message_id": 7,
        "conversation_id": "-100",
        "question": "How?",
        "profile_id": None,
    }
    payload.update(overrides)
    return payload


def test_profiles_message_profile_update_and_feedback_endpoints(tmp_path: Path) -> None:
    service, chat, store, audit = gateway(tmp_path)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(application, headers=AUTH_HEADERS) as client:
        profiles = client.get("/v1/profiles")
        selected = client.put("/v1/users/42/profile", json={"profile_id": "support-v1"})
        message = client.post("/v1/messages", json=_message())
        mismatch = client.post(
            "/v1/feedback",
            json={
                "request_id": "request-1",
                "telegram_user_id": 42,
                "conversation_id": "-200",
                "rating": 5,
                "comment": None,
            },
        )
        feedback_payload = {
            "request_id": "request-1",
            "telegram_user_id": 42,
            "conversation_id": "-100",
            "rating": 5,
            "comment": None,
        }
        feedback = client.post("/v1/feedback", json=feedback_payload)
        duplicate = client.post("/v1/feedback", json=feedback_payload)

    assert profiles.status_code == 200
    assert profiles.json() == {
        "profiles": [
            {
                "profile_id": "support-v1",
                "display_name": "support-v1",
                "is_default": True,
            }
        ],
        "default_profile_id": "support-v1",
    }
    assert selected.status_code == 200
    assert store.values == {42: "support-v1"}
    assert message.status_code == 200
    assert message.json()["request_id"] == "request-1"
    assert message.json()["profile_id"] == "support-v1"
    assert message.json()["sources"][0]["document_id"] == "document"
    assert chat.requests[0].question == "How?"
    assert chat.requests[0].question == chat.requests[0].search.question
    assert mismatch.status_code == 404
    assert feedback.status_code == 200
    assert duplicate.status_code == 404

    backend_lines = audit.backend_writer.path.read_text().splitlines()
    feedback_lines = audit.feedback_writer.path.read_text().splitlines()
    backend = BackendEvent.model_validate_json(backend_lines[0])
    accepted = FeedbackEvent.model_validate_json(feedback_lines[0])
    assert backend.request_id == "request-1"
    assert backend.conversation_id == "-100"
    assert backend.database_name == "gateway-test"
    assert backend.answer == "Verified answer."
    assert accepted.rating == 5


def test_message_rejects_unknown_profile_without_calling_rag(tmp_path: Path) -> None:
    service, chat, _, _ = gateway(tmp_path)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post("/v1/messages", json=_message(profile_id="unknown-v1"))

    assert response.status_code == 422
    assert chat.requests == []
    assert "unknown-v1" not in response.text


def test_profile_update_rejects_unknown_profile(tmp_path: Path) -> None:
    service, chat, store, _ = gateway(tmp_path)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.put("/v1/users/42/profile", json={"profile_id": "unknown-v1"})

    assert response.status_code == 422
    assert store.values == {}
    assert chat.requests == []
    assert "unknown-v1" not in response.text


def test_unauthorized_user_is_rejected_before_rag(tmp_path: Path) -> None:
    service, chat, _, audit = gateway(tmp_path)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post("/v1/messages", json=_message(telegram_user_id=7))

    assert response.status_code == 403
    assert chat.requests == []
    record = BackendEvent.model_validate_json(
        audit.backend_writer.path.read_text().splitlines()[0]
    )
    assert record.status == "rejected"


def test_backend_failure_is_redacted_and_not_retried(tmp_path: Path) -> None:
    failing = FakeChat(RuntimeError("secret-internal-path"))
    service, _, _, audit = gateway(tmp_path, chat=failing)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(
        application, headers=AUTH_HEADERS, raise_server_exceptions=False
    ) as client:
        response = client.post("/v1/messages", json=_message())

    assert response.status_code == 503
    assert "secret-internal-path" not in response.text
    assert len(failing.requests) == 1
    error_text = audit.error_writer.path.read_text()
    assert "RuntimeError: secret-internal-path" in error_text


def test_overload_returns_503_without_calling_rag(tmp_path: Path) -> None:
    capacity = CapacityLimiter(1, {"chat": 1})
    service, chat, _, _ = gateway(tmp_path, capacity=capacity)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with (
        capacity.acquire("chat"),
        TestClient(application, headers=AUTH_HEADERS) as client,
    ):
        response = client.post("/v1/messages", json=_message())

    assert response.status_code == 503
    assert response.json()["error"] == "overloaded"
    assert chat.requests == []


def test_feedback_validates_rating_and_known_request(tmp_path: Path) -> None:
    service, _, _, audit = gateway(tmp_path)
    application = create_application(
        gateway=service, readiness=ReadyProbe(), api_token=API_TOKEN
    )

    with TestClient(application, headers=AUTH_HEADERS) as client:
        invalid = client.post(
            "/v1/feedback",
            json={
                "request_id": "missing",
                "telegram_user_id": 42,
                "conversation_id": "-100",
                "rating": 6,
            },
        )
        unknown = client.post(
            "/v1/feedback",
            json={
                "request_id": "missing",
                "telegram_user_id": 42,
                "conversation_id": "-100",
                "rating": 3,
            },
        )

    assert invalid.status_code == 422
    assert unknown.status_code == 404
    validation_event = ErrorEvent.model_validate_json(
        audit.error_writer.path.read_text().splitlines()[0]
    )
    assert validation_event.operation == "request_validation"
    assert validation_event.request_id == "missing"


def test_health_uses_200_and_503_for_ready_state(tmp_path: Path) -> None:
    service, _, _, _ = gateway(tmp_path)
    ready = create_application(
        gateway=service, readiness=ReadyProbe(True), api_token=API_TOKEN
    )
    unready = create_application(
        gateway=service, readiness=ReadyProbe(False), api_token=API_TOKEN
    )

    with TestClient(ready, headers=AUTH_HEADERS) as client:
        assert client.get("/health").status_code == 200
    with TestClient(unready, headers=AUTH_HEADERS) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_v1_endpoints_require_service_bearer_token(tmp_path: Path) -> None:
    service, _, _, _ = gateway(tmp_path)
    application = create_application(
        gateway=service,
        readiness=ReadyProbe(),
        api_token="x" * 32,
    )

    with TestClient(application) as client:
        missing = client.get("/v1/profiles")
        wrong = client.get(
            "/v1/profiles", headers={"Authorization": "Bearer " + "y" * 32}
        )
        accepted = client.get(
            "/v1/profiles", headers={"Authorization": "Bearer " + "x" * 32}
        )
        health = client.get("/health")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200
    assert health.status_code == 200
    assert "y" * 32 not in wrong.text
