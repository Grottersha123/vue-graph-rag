from __future__ import annotations

import hashlib
import http.client
import ipaddress
import shutil
import socket
import ssl
import subprocess
import tempfile
import typing
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image, UnidentifiedImageError

from mvp_v2.application.ingestion_contracts import (
    ExtractedAsset,
    ExtractedInput,
    IngestionRequest,
)
from mvp_v2.ports.ingestion import UrlFetcher


class ExtractionError(RuntimeError):
    pass


class _ConnectionFactory(typing.Protocol):
    def __call__(
        self,
        scheme: str,
        address: str,
        port: int,
        hostname: str,
        timeout: float,
    ) -> http.client.HTTPConnection: ...


class _PinnedHttpConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        super().__init__(hostname, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float) -> None:
        ssl_context = ssl.create_default_context()
        super().__init__(hostname, port, timeout=timeout, context=ssl_context)
        self._address = address
        self._ssl_context = ssl_context

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._ssl_context.wrap_socket(
                raw_socket, server_hostname=self.host
            )
        except (OSError, ValueError):
            raw_socket.close()
            raise


def _create_connection(
    scheme: str,
    address: str,
    port: int,
    hostname: str,
    timeout: float,
) -> http.client.HTTPConnection:
    if scheme == "https":
        return _PinnedHttpsConnection(hostname, address, port, timeout)
    return _PinnedHttpConnection(hostname, address, port, timeout)


def _resolve_addresses(hostname: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(
        hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return tuple(dict.fromkeys(typing.cast(str, record[4][0]) for record in records))


class PublicHttpUrlFetcher:
    """Fetch bounded public HTTP(S) content without DNS rebinding exposure."""

    _REDIRECT_CODES: typing.Final = frozenset({301, 302, 303, 307, 308})
    _CONTENT_TYPES: typing.Final = frozenset({"text/html", "text/plain"})

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = 20_000_000,
        max_redirects: int = 5,
        resolver: typing.Callable[[str, int], tuple[str, ...]] = _resolve_addresses,
        connection_factory: _ConnectionFactory = _create_connection,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver
        self._connection_factory = connection_factory

    def fetch(self, url: str) -> tuple[bytes, str]:
        current_url = url
        for redirect_count in range(self._max_redirects + 1):
            scheme, hostname, port, target = self._validate_url(current_url)
            addresses = self._resolve_public(hostname, port)
            connection = self._connection_factory(
                scheme, addresses[0], port, hostname, self._timeout
            )
            try:
                connection.request(
                    "GET",
                    target,
                    headers={"Host": self._host_header(hostname, port, scheme)},
                )
                response = connection.getresponse()
                if response.status in self._REDIRECT_CODES:
                    location = response.getheader("Location")
                    response.close()
                    if location is None or redirect_count == self._max_redirects:
                        raise ExtractionError(
                            "URL redirect is invalid or exceeds limit"
                        )
                    current_url = urljoin(current_url, location)
                    continue
                if not 200 <= response.status < 300:
                    raise ExtractionError("URL server returned an unsupported status")
                content_type = response.getheader("Content-Type")
                media_type = (
                    content_type.split(";", 1)[0].strip().lower()
                    if content_type
                    else ""
                )
                if media_type not in self._CONTENT_TYPES:
                    raise ExtractionError("URL response content type is unsupported")
                content = self._read_bounded(response)
                return content, media_type
            except ExtractionError:
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError) as error:
                raise ExtractionError("public URL fetch failed") from error
            finally:
                connection.close()
        raise ExtractionError("URL redirect exceeds limit")

    def _resolve_public(self, hostname: str, port: int) -> tuple[str, ...]:
        try:
            addresses = self._resolver(hostname, port)
            parsed_addresses = tuple(ipaddress.ip_address(item) for item in addresses)
        except (OSError, ValueError) as error:
            raise ExtractionError("public URL host cannot be resolved") from error
        if not parsed_addresses or any(
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            for address in parsed_addresses
        ):
            raise ExtractionError("URL host is not public")
        return tuple(str(address) for address in parsed_addresses)

    @staticmethod
    def _validate_url(url: str) -> tuple[str, str, int, str]:
        if not url or url != url.strip() or any(ord(char) < 32 for char in url):
            raise ExtractionError("URL is invalid")
        try:
            parsed = urlsplit(url)
            port = parsed.port
            hostname = parsed.hostname
        except ValueError as error:
            raise ExtractionError("URL is invalid") from error
        scheme = parsed.scheme.lower()
        expected_port = 80 if scheme == "http" else 443 if scheme == "https" else None
        if (
            expected_port is None
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or (port is not None and port != expected_port)
        ):
            raise ExtractionError("URL is invalid")
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ExtractionError("URL hostname is invalid") from error
        if not ascii_hostname or len(ascii_hostname) > 253:
            raise ExtractionError("URL hostname is invalid")
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        return scheme, ascii_hostname, expected_port, target

    def _read_bounded(self, response: http.client.HTTPResponse) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while chunk := response.read(min(64 * 1024, self._max_bytes - total + 1)):
            chunks.append(chunk)
            total += len(chunk)
            if total > self._max_bytes:
                raise ExtractionError("URL response exceeds configured limit")
        return b"".join(chunks)

    @staticmethod
    def _host_header(hostname: str, port: int, scheme: str) -> str:
        del port, scheme
        return f"[{hostname}]" if ":" in hostname else hostname


# Backward-compatible name used by the existing application composition.
LocalHttpUrlFetcher = PublicHttpUrlFetcher


class _ReadableHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)


def html_text(raw: bytes) -> str:
    parser = _ReadableHtml()
    try:
        parser.feed(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ExtractionError("HTML must be valid UTF-8") from error
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


class LocalInputExtractor:
    def __init__(
        self,
        input_root: Path,
        url_fetcher: UrlFetcher | None = None,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = 20_000_000,
    ) -> None:
        self._root = input_root.resolve()
        self._fetcher = url_fetcher
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    def extract(self, request: IngestionRequest) -> ExtractedInput:
        if request.kind == "url":
            if self._fetcher is None:
                raise ExtractionError("URL fetching is not configured")
            raw, content_type = self._fetcher.fetch(request.source_url)
            self._bounded(raw)
            return ExtractedInput(
                content=raw,
                text=html_text(raw) if "html" in content_type else self._utf8(raw),
                extension=".html" if "html" in content_type else ".txt",
            )
        path = self._safe_input(request.input_path)
        if request.kind == "image_collection":
            if not path.is_dir():
                raise ExtractionError("image collection input must be a directory")
            assets = tuple(self._asset(item) for item in self._image_files(path))
            manifest_lines = ["image_collection-v1"]
            manifest_lines.extend(
                f"{item.name}\t{item.mime_type}\t{hashlib.sha256(item.content).hexdigest()}"
                for item in assets
            )
            manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
            return ExtractedInput(
                content=manifest,
                text="",
                extension=".manifest",
                assets=assets,
            )
        raw = self._read(path)
        self._bounded(raw)
        if request.kind == "pdf":
            return ExtractedInput(
                content=raw, text=self._pdf_text(path), extension=".pdf"
            )
        suffix = ".md" if request.kind == "markdown" else ".html"
        return ExtractedInput(
            content=raw,
            text=html_text(raw) if request.kind == "html" else self._utf8(raw),
            extension=suffix,
        )

    def _safe_input(self, relative: str) -> Path:
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ExtractionError("input path must be safe and relative")
        path = self._root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self._root):
            raise ExtractionError("input symlinks and traversal are refused")
        return path

    def _image_files(self, path: Path) -> tuple[Path, ...]:
        try:
            return tuple(item for item in sorted(path.iterdir()) if item.is_file())
        except OSError as error:
            raise ExtractionError("image collection cannot be read") from error

    @staticmethod
    def _read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as error:
            raise ExtractionError("input file cannot be read") from error

    def _asset(self, path: Path) -> ExtractedAsset:
        if path.is_symlink():
            raise ExtractionError("image symlinks are refused")
        raw = self._read(path)
        self._bounded(raw)
        mime = self._validated_image_mime(raw)
        return ExtractedAsset(name=path.name, content=raw, mime_type=mime)

    @staticmethod
    def _validated_image_mime(raw: bytes) -> str:
        formats = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "GIF": "image/gif",
            "WEBP": "image/webp",
        }
        try:
            with Image.open(BytesIO(raw)) as image:
                image_format = image.format
                width, height = image.size
                if width < 1 or height < 1 or width * height > 40_000_000:
                    raise ExtractionError("image dimensions are unsupported")
                image.verify()
        except ExtractionError:
            raise
        except (
            Image.DecompressionBombError,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
        ) as error:
            raise ExtractionError("image content is invalid or unsupported") from error
        mime = formats.get(image_format or "")
        if mime is None:
            raise ExtractionError("image content is invalid or unsupported")
        return mime

    def _pdf_text(self, path: Path) -> str:
        if shutil.which("pdfinfo") is None or shutil.which("pdftotext") is None:
            raise ExtractionError("PDF tools pdfinfo and pdftotext are unavailable")
        try:
            subprocess.run(
                ["pdfinfo", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=self._timeout,
            )
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "text.txt"
                subprocess.run(
                    ["pdftotext", str(path), str(output)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=self._timeout,
                )
                if output.stat().st_size > self._max_bytes:
                    raise ExtractionError("PDF text exceeds configured byte limit")
                raw = self._read(output)
        except ExtractionError:
            raise
        except (subprocess.SubprocessError, OSError) as error:
            raise ExtractionError("PDF text extraction failed") from error
        return self._utf8(raw)

    def _bounded(self, raw: bytes) -> None:
        if len(raw) > self._max_bytes:
            raise ExtractionError("input exceeds configured byte limit")

    @staticmethod
    def _utf8(raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ExtractionError("text input must be valid UTF-8") from error
