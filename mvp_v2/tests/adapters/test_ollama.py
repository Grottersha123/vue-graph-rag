from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from mvp_v2.adapters import ollama
from mvp_v2.adapters.ollama import LocalHttpTransport, OllamaAdapter, OllamaError


@dataclass
class Response:
    status: int
    body: bytes
    close_failure: BaseException | None = None
    closed: bool = False

    def read(self, amount: int | None = None) -> bytes:
        return self.body if amount is None else self.body[:amount]

    def close(self) -> None:
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


@dataclass
class Connection:
    response: Response
    failure: BaseException | None = None
    requests: list[tuple[str, str, bytes | None, dict[str, str] | None]] = field(
        default_factory=list
    )
    closed: bool = False

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self.failure is not None:
            raise self.failure
        self.requests.append((method, url, body, headers))

    def getresponse(self) -> Response:
        return self.response

    def close(self) -> None:
        self.closed = True


@dataclass
class Factory:
    connection: Connection
    calls: list[tuple[str, str, int, float]] = field(default_factory=list)

    def __call__(
        self, hostname: str, address: str, port: int, timeout: float
    ) -> Connection:
        self.calls.append((hostname, address, port, timeout))
        return self.connection


def test_transport_diagnostics_cleanup_and_connection_failures() -> None:
    http_response = Response(
        404,
        b'{"error":"model not found"}',
        close_failure=RuntimeError("close failed"),
    )
    http_connection = Connection(http_response)
    with pytest.raises(
        OllamaError, match="local Ollama returned HTTP 404: model not found"
    ):
        LocalHttpTransport(
            "http://127.0.0.1:11434",
            connection_factory=Factory(http_connection),
        ).post("/api/generate", b"{}")
    assert http_response.closed
    assert http_connection.closed

    refused = Connection(Response(200, b"{}"), failure=OSError("refused"))
    with pytest.raises(OllamaError, match="local Ollama connection failed"):
        LocalHttpTransport(
            "http://127.0.0.1:11434",
            connection_factory=Factory(refused),
        ).post("/api/generate", b"{}")
    assert refused.closed

    timed_out = Connection(Response(200, b"{}"), failure=TimeoutError())
    with pytest.raises(OllamaError, match="timed out after 2s"):
        LocalHttpTransport(
            "http://127.0.0.1:11434",
            timeout_seconds=2,
            connection_factory=Factory(timed_out),
        ).post("/api/generate", b"{}")
    assert timed_out.closed


def test_origin_policy_rejects_unauthorized_or_unsafe_resolution() -> None:
    with pytest.raises(ValueError, match="explicit authorization"):
        LocalHttpTransport("http://models.local:11434")

    with pytest.raises(ValueError, match="private addresses"):
        LocalHttpTransport(
            "http://models.local:11434",
            allowed_lan_hosts=frozenset({"models.local"}),
            resolver=lambda hostname, port: ("192.168.1.20", "8.8.8.8"),
        )

    with pytest.raises(ValueError, match="only to loopback"):
        LocalHttpTransport(
            "http://localhost:11434",
            resolver=lambda hostname, port: ("192.168.1.20",),
        )


def test_transport_pins_validated_address_and_preserves_host_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Connection(Response(200, b"{}"))
    factory = Factory(connection)
    transport = LocalHttpTransport(
        "http://models.local:11434",
        allowed_lan_hosts=frozenset({"models.local"}),
        resolver=lambda hostname, port: ("192.168.1.20",),
        connection_factory=factory,
    )

    assert transport.post("/api/generate", b"{}") == b"{}"
    assert factory.calls == [("models.local", "192.168.1.20", 11434, 30.0)]
    assert connection.requests == [
        (
            "POST",
            "/api/generate",
            b"{}",
            {"Content-Type": "application/json", "Host": "models.local:11434"},
        )
    ]

    calls: list[tuple[tuple[str, int], float | None]] = []
    socket_marker = object()

    def create_connection(
        address: tuple[str, int], timeout: float | None = None
    ) -> object:
        calls.append((address, timeout))
        return socket_marker

    monkeypatch.setattr(ollama.socket, "create_connection", create_connection)
    pinned = ollama._PinnedHttpConnection("models.local", "192.168.1.20", 11434, 7.0)
    pinned.connect()

    assert calls == [(("192.168.1.20", 11434), 7.0)]
    assert pinned.sock is socket_marker


def test_adapter_sends_deterministic_generation_temperature() -> None:
    class RecordingTransport:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def post(self, path: str, payload: bytes) -> bytes:
            assert path == "/api/generate"
            decoded = json.loads(payload)
            assert isinstance(decoded, dict)
            self.payload = decoded
            return b'{"response":"ok"}'

    transport = RecordingTransport()
    adapter = OllamaAdapter(transport, generation_temperature=0.0)

    assert adapter.generate(prompt="question", model="gemma4:latest") == "ok"
    assert transport.payload == {
        "model": "gemma4:latest",
        "prompt": "question",
        "stream": False,
        "options": {"temperature": 0.0},
    }
    with pytest.raises(ValueError, match="temperature"):
        OllamaAdapter(transport, generation_temperature=float("nan"))
