from __future__ import annotations

import hashlib
import mimetypes
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from mvp_v2.application.ingestion_contracts import (
    ExtractedAsset,
    ExtractedInput,
    IngestionRequest,
)
from mvp_v2.ports.ingestion import UrlFetcher


class ExtractionError(RuntimeError):
    pass


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
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise ExtractionError("image collection contains a non-image file")
        return ExtractedAsset(name=path.name, content=raw, mime_type=mime)

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
