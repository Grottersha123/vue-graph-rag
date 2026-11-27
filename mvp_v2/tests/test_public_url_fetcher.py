from __future__ import annotations

import collections
import http.client
import typing

import pytest

from mvp_v2.adapters.ingestion import ExtractionError, PublicHttpUrlFetcher


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"hello",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self._offset = 0
        self._headers = {"Content-Type": "text/plain"} if headers is None else headers

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._body)
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        url: str,
        body: typing.Any = None,
        headers: dict[str, str] | None = None,
        *,
        encode_chunked: bool = False,
    ) -> None:
        del body, encode_chunked
        self.requests.append((method, url, headers or {}))

    def getresponse(self) -> typing.Any:
        return self.response

    def close(self) -> None:
        return None


class FakeFactory:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = collections.deque(responses)
        self.calls: list[tuple[str, str, int, str, float]] = []
        self.connections: list[FakeConnection] = []

    def __call__(
        self,
        scheme: str,
        address: str,
        port: int,
        hostname: str,
        timeout: float,
    ) -> http.client.HTTPConnection:
        self.calls.append((scheme, address, port, hostname, timeout))
        connection = FakeConnection(self.responses.popleft())
        self.connections.append(connection)
        return typing.cast(http.client.HTTPConnection, connection)


def public_resolver(hostname: str, port: int) -> tuple[str, ...]:
    del hostname, port
    return ("93.184.216.34",)


def test_fetches_public_ipv4_with_hostname_host_header() -> None:
    factory = FakeFactory([FakeResponse(body=b"public")])
    fetcher = PublicHttpUrlFetcher(resolver=public_resolver, connection_factory=factory)

    assert fetcher.fetch("http://example.com/path?q=1") == (b"public", "text/plain")
    assert factory.calls[0][:4] == ("http", "93.184.216.34", 80, "example.com")
    assert factory.connections[0].requests == [
        ("GET", "/path?q=1", {"Host": "example.com"})
    ]


def test_https_factory_receives_original_hostname_for_sni() -> None:
    factory = FakeFactory(
        [FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"})]
    )
    fetcher = PublicHttpUrlFetcher(resolver=public_resolver, connection_factory=factory)

    assert fetcher.fetch("https://Example.COM/") == (b"hello", "text/html")
    assert factory.calls[0][:4] == ("https", "93.184.216.34", 443, "example.com")
    assert factory.connections[0].requests[0][2] == {"Host": "example.com"}


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff00::1",
        "::",
    ],
)
def test_rejects_every_non_public_address_class(address: str) -> None:
    fetcher = PublicHttpUrlFetcher(resolver=lambda hostname, port: (address,))

    with pytest.raises(ExtractionError, match="not public"):
        fetcher.fetch("https://example.com")


def test_rejects_mixed_public_and_private_dns_answers() -> None:
    fetcher = PublicHttpUrlFetcher(
        resolver=lambda hostname, port: ("93.184.216.34", "192.168.1.1")
    )

    with pytest.raises(ExtractionError, match="not public"):
        fetcher.fetch("https://example.com")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "http://user@example.com/",
        "http://user:pass@example.com/",
        "http://example.com:81/",
        "https://example.com:444/",
        "https://example.com/#fragment",
        "https://example.com/\n",
    ],
)
def test_rejects_invalid_url_forms(url: str) -> None:
    fetcher = PublicHttpUrlFetcher(resolver=public_resolver)

    with pytest.raises(ExtractionError, match="URL is invalid"):
        fetcher.fetch(url)


def test_redirect_revalidates_and_blocks_private_destination() -> None:
    factory = FakeFactory(
        [FakeResponse(status=302, headers={"Location": "http://internal.test/secret"})]
    )

    def resolver(hostname: str, port: int) -> tuple[str, ...]:
        del port
        return ("93.184.216.34",) if hostname == "example.com" else ("10.0.0.1",)

    fetcher = PublicHttpUrlFetcher(resolver=resolver, connection_factory=factory)

    with pytest.raises(ExtractionError, match="not public"):
        fetcher.fetch("http://example.com")
    assert len(factory.calls) == 1


def test_redirect_loop_is_capped() -> None:
    responses = [
        FakeResponse(status=302, headers={"Location": "/again"}) for _ in range(6)
    ]
    factory = FakeFactory(responses)
    fetcher = PublicHttpUrlFetcher(resolver=public_resolver, connection_factory=factory)

    with pytest.raises(ExtractionError, match="redirect"):
        fetcher.fetch("http://example.com")
    assert len(factory.calls) == 6


def test_streamed_body_is_capped() -> None:
    factory = FakeFactory([FakeResponse(body=b"123456")])
    fetcher = PublicHttpUrlFetcher(
        max_bytes=5, resolver=public_resolver, connection_factory=factory
    )

    with pytest.raises(ExtractionError, match="exceeds configured limit"):
        fetcher.fetch("http://example.com")


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "application/json"}])
def test_rejects_missing_or_unsupported_content_type(headers: dict[str, str]) -> None:
    factory = FakeFactory([FakeResponse(headers=headers)])
    fetcher = PublicHttpUrlFetcher(resolver=public_resolver, connection_factory=factory)

    with pytest.raises(ExtractionError, match="content type"):
        fetcher.fetch("http://example.com")
