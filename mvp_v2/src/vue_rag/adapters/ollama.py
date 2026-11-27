from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import math
import socket
from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import urlparse


class OllamaError(RuntimeError):
    pass


class JsonTransport(Protocol):
    def post(self, path: str, payload: bytes) -> bytes: ...


class HttpResponse(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class HttpConnection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None: ...

    def getresponse(self) -> HttpResponse: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, str, int, float], HttpConnection]
AddressResolver = Callable[[str, int], tuple[str, ...]]


class _PinnedHttpConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


def _connection_factory(
    hostname: str, address: str, port: int, timeout: float
) -> HttpConnection:
    return cast(HttpConnection, _PinnedHttpConnection(hostname, address, port, timeout))


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        records = socket.getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
    except OSError as error:
        raise ValueError("Ollama host cannot be resolved") from error
    return tuple(dict.fromkeys(cast(str, record[4][0]) for record in records))


def _parsed_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_loopback_address(value: str) -> bool:
    address = _parsed_address(value)
    return address is not None and address.is_loopback


def _is_private_address(value: str) -> bool:
    address = _parsed_address(value)
    return address is not None and (address.is_private or address.is_link_local)


def _host_header(hostname: str, port: int) -> str:
    rendered = f"[{hostname}]" if ":" in hostname else hostname
    return rendered if port == 80 else f"{rendered}:{port}"


class LocalHttpTransport:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_body_bytes: int = 4_000_000,
        allowed_lan_hosts: frozenset[str] = frozenset(),
        resolver: AddressResolver | None = None,
        connection_factory: ConnectionFactory = _connection_factory,
    ) -> None:
        parsed = urlparse(base_url)
        hostname = parsed.hostname.casefold() if parsed.hostname else None
        normalized_lan_hosts = frozenset(
            item.casefold() for item in allowed_lan_hosts if item == item.strip()
        )
        if (
            parsed.scheme != "http"
            or hostname is None
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama URL must be an authorized HTTP origin")
        port = parsed.port or 80
        resolve = resolver or _resolve_addresses
        addresses: tuple[str, ...]
        if hostname in {"127.0.0.1", "::1"}:
            addresses = (hostname,)
        elif hostname == "localhost":
            addresses = resolve(hostname, port)
            if not addresses or any(
                not _is_loopback_address(item) for item in addresses
            ):
                raise ValueError("Ollama localhost must resolve only to loopback")
        else:
            if hostname not in normalized_lan_hosts:
                raise ValueError("Ollama LAN host requires explicit authorization")
            addresses = resolve(hostname, port)
            if not addresses or any(
                not _is_private_address(item) for item in addresses
            ):
                raise ValueError(
                    "Ollama LAN host must resolve only to private addresses"
                )
        self._hostname = hostname
        self._address = addresses[0]
        self._port = port
        self._host_header = _host_header(hostname, port)
        self._timeout = timeout_seconds
        self._max_body = max_body_bytes
        self._connection_factory = connection_factory

    def post(self, path: str, payload: bytes) -> bytes:
        if not path.startswith("/") or "://" in path or "\r" in path or "\n" in path:
            raise ValueError("Ollama request path is invalid")
        connection = self._connection_factory(
            self._hostname,
            self._address,
            self._port,
            self._timeout,
        )
        response: HttpResponse | None = None
        try:
            connection.request(
                "POST",
                path,
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "Host": self._host_header,
                },
            )
            response = connection.getresponse()
            body = response.read(self._max_body + 1)
            if len(body) > self._max_body:
                raise OllamaError("local Ollama response exceeds configured limit")
            if not 200 <= response.status < 300:
                detail = _http_error_detail(body, self._max_body)
                message = f"local Ollama returned HTTP {response.status}"
                if detail:
                    message += f": {detail}"
                raise OllamaError(message)
            return body
        except OllamaError:
            raise
        except TimeoutError as error:
            raise OllamaError(
                f"local Ollama request timed out after {self._timeout:g}s"
            ) from error
        except (OSError, http.client.HTTPException) as error:
            raise OllamaError("local Ollama connection failed") from error
        finally:
            try:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            finally:
                try:
                    connection.close()
                except Exception:
                    pass


class OllamaAdapter:
    def __init__(
        self,
        transport: JsonTransport,
        *,
        generation_temperature: float = 0.0,
    ) -> None:
        if (
            isinstance(generation_temperature, bool)
            or not math.isfinite(generation_temperature)
            or not 0.0 <= generation_temperature <= 2.0
        ):
            raise ValueError("generation temperature must be between 0 and 2")
        self._transport = transport
        self._generation_temperature = float(generation_temperature)

    def describe(self, *, image: bytes, mime_type: str, model: str) -> str:
        del mime_type
        payload = {
            "model": model,
            "prompt": "Describe this image for factual retrieval.",
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
        }
        result = self._object("/api/generate", payload)
        response = result.get("response")
        if not isinstance(response, str):
            raise OllamaError("invalid Ollama generation response")
        return response

    def generate(self, *, prompt: str, model: str) -> str:
        result = self._object(
            "/api/generate",
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": self._generation_temperature},
            },
        )
        response = result.get("response")
        if not isinstance(response, str):
            raise OllamaError("invalid Ollama generation response")
        return response

    def embed(
        self, *, texts: tuple[str, ...], model: str
    ) -> tuple[tuple[float, ...], ...]:
        result = self._object("/api/embed", {"model": model, "input": texts})
        raw = result.get("embeddings")
        if not isinstance(raw, list):
            raise OllamaError("invalid Ollama embedding response")
        try:
            return tuple(
                tuple(_number(value) for value in cast(list[object], vector))
                for vector in raw
                if isinstance(vector, list)
            )
        except (TypeError, ValueError) as error:
            raise OllamaError("invalid Ollama embedding response") from error

    def _object(self, path: str, payload: object) -> dict[str, object]:
        try:
            value = json.loads(
                self._transport.post(path, json.dumps(payload).encode("utf-8"))
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OllamaError("invalid JSON from local Ollama") from error
        if not isinstance(value, dict):
            raise OllamaError("invalid JSON object from local Ollama")
        return cast(dict[str, object], value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("embedding component is not numeric")
    return float(value)


def _http_error_detail(body: bytes, max_body_bytes: int) -> str:
    limit = min(max_body_bytes, 16_384)
    if len(body) > limit:
        return "response detail exceeds limit"
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), str):
        return ""
    detail = cast(str, payload["error"])
    sanitized = " ".join(detail.replace("\x00", " ").split())
    return sanitized[:500]
