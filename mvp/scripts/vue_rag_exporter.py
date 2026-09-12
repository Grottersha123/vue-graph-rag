#!/usr/bin/env python3
"""Download a small public-document graph and export it to LadybugDB CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import mimetypes
import re
import shutil
import socket
import sys
import traceback
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from .database import DatabaseError, GraphLoader
    from .utils import (
        canonical_url,
        deduplicate_documents,
        document_id_for_url,
        document_identity,
        external_identity,
    )
    from .schemas import (
        ASSET_COLUMNS,
        DOCUMENT_COLUMNS,
        FOR_ROLE_COLUMNS,
        MANIFEST_COLUMNS,
        MANUAL_LINK_COLUMNS,
        ROLE_COLUMNS,
        VERSION_COLUMNS,
        AssetRecord,
        DocumentRecord,
        DocumentVersionRecord,
        DownloadedDocument,
        ImageCandidate,
        ImageManifestRecord,
        ManualLinkRecord,
        ParsedDocument,
        RoleRecord,
    )
except ImportError:  # Direct execution: python mvp/scripts/vue_rag_exporter.py
    from database import DatabaseError, GraphLoader
    from utils import (
        canonical_url,
        deduplicate_documents,
        document_id_for_url,
        document_identity,
        external_identity,
    )
    from schemas import (
        ASSET_COLUMNS,
        DOCUMENT_COLUMNS,
        FOR_ROLE_COLUMNS,
        MANIFEST_COLUMNS,
        MANUAL_LINK_COLUMNS,
        ROLE_COLUMNS,
        VERSION_COLUMNS,
        AssetRecord,
        DocumentRecord,
        DocumentVersionRecord,
        DownloadedDocument,
        ImageCandidate,
        ImageManifestRecord,
        ManualLinkRecord,
        ParsedDocument,
        RoleRecord,
    )

ROOT_DIR = Path(__file__).resolve().parents[2]
MVP_DIR = ROOT_DIR / "mvp"
DATA_DIR = MVP_DIR / "mvp_data"
DOCUMENTS_DIR = DATA_DIR / "documents"
RAW_DIR = DATA_DIR / "raw"
IMAGES_DIR = DATA_DIR / "images"
DATABASE_PATH = DATA_DIR / "vue_rag.lbdb"
ROOT_DOCUMENT_PATH = ROOT_DIR / "research" / "Инструкция по онбордингу.pdf"

IMAGE_EXTENSIONS = {".apng", ".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
DOCUMENT_EXTENSIONS = {".html", ".htm", ".md", ".markdown", ".pdf"}
LOGIN_MARKERS = ("/login", "/signin", "/sign-in", "/oauth", "/authorize", "/auth/")
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_REDIRECTS = 5


def _safe_project_path(value: str) -> Path | None:
    """Resolve a stored path without ever treating an empty value as ROOT_DIR."""
    if not value:
        return None
    candidate = Path(value)
    resolved = (candidate if candidate.is_absolute() else ROOT_DIR / candidate).resolve()
    if resolved == ROOT_DIR or not resolved.is_relative_to(ROOT_DIR):
        return None
    return resolved


class ExporterError(RuntimeError):
    """A user-actionable exporter error."""


class DownloadError(ExporterError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CsvStore:
    """Read and atomically rewrite the CSV state used as the graph source."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.documents: dict[str, DocumentRecord] = {}
        self.versions: dict[str, DocumentVersionRecord] = {}
        self.assets: dict[str, AssetRecord] = {}
        self.roles: dict[str, RoleRecord] = {}
        self.manual_links: dict[tuple[str, str], ManualLinkRecord] = {}
        self.image_manifest: dict[tuple[str, str], ImageManifestRecord] = {}
        self.links: set[tuple[str, str]] = set()
        self.role_links: set[tuple[str, str]] = set()
        self._load()

    def _read_rows(self, name: str) -> Iterator[dict[str, str]]:
        path = self.data_dir / name
        if not path.exists():
            return
        with path.open("r", encoding="utf-8", newline="") as handle:
            yield from csv.DictReader(handle)

    def _load(self) -> None:
        for row in self._read_rows("Document.csv"):
            if row.get("id"):
                try:
                    self.documents[row["id"]] = DocumentRecord.model_validate(row)
                except Exception:
                    continue
        for row in self._read_rows("DocumentVersion.csv"):
            if row.get("id"):
                try:
                    self.versions[row["id"]] = DocumentVersionRecord.model_validate(row)
                except Exception:
                    continue
        for row in self._read_rows("Asset.csv"):
            if row.get("id"):
                try:
                    self.assets[row["id"]] = AssetRecord.model_validate(row)
                except Exception:
                    continue
        for row in self._read_rows("Role.csv"):
            if row.get("id"):
                try:
                    self.roles[row["id"]] = RoleRecord.model_validate(row)
                except Exception:
                    continue
        for row in self._read_rows("FOR_ROLE.csv"):
            if row.get("from") and row.get("to"):
                self.role_links.add((row["from"], row["to"]))
        for row in self._read_rows("manual_links.csv"):
            if row.get("source_document_id") and row.get("target_external_id"):
                try:
                    record = ManualLinkRecord.model_validate(row)
                except Exception:
                    continue
                self.manual_links[
                    (record.source_document_id, record.target_external_id)
                ] = record
        for row in self._read_rows("image_manifest.csv"):
            if row.get("source_document_id") and row.get("source_url"):
                try:
                    record = ImageManifestRecord.model_validate(row)
                except Exception:
                    continue
                self.image_manifest[(record.source_document_id, record.source_url)] = (
                    record
                )
        for row in self._read_rows("LINKS_TO.csv"):
            if row.get("from") and row.get("to"):
                self.links.add((row["from"], row["to"]))

    def find_document_id(self, url: str) -> str | None:
        identity = external_identity(url)
        matches = sorted(
            document.id
            for document in self.documents.values()
            if document.source_type == "external_web"
            and external_identity(document.source_url) == identity
        )
        return matches[0] if matches else None

    def put_document(self, record: DocumentRecord) -> None:
        self.documents[record.id] = record

    def put_role(self, document_id: str, role_name: str) -> None:
        role = RoleRecord(id=role_name, name=role_name)
        self.roles[role.id] = role
        self.role_links.add((document_id, role.id))

    def put_version(self, record: DocumentVersionRecord) -> None:
        for version in self.versions.values():
            if version.id.startswith(f"{record.id.split(':v:')[0]}:v:"):
                version.is_current = False
        self.versions[record.id] = record

    def put_asset(self, record: AssetRecord) -> None:
        self.assets[record.id] = record

    def put_manual_link(self, record: ManualLinkRecord) -> None:
        self.manual_links[(record.source_document_id, record.target_external_id)] = (
            record
        )

    def put_manifest(self, record: ImageManifestRecord) -> None:
        self.image_manifest[(record.source_document_id, record.source_url)] = record

    def remove_document(self, document_id: str) -> None:
        document = self.documents.pop(document_id, None)
        if document:
            document_path = _safe_project_path(document.local_path)
            if document_path and document_path.is_file():
                document_path.unlink()
            if document.source_type == "local_file" and document.source_url.startswith(
                "file://"
            ):
                source_path = (
                    ROOT_DIR / document.source_url.removeprefix("file://")
                ).resolve()
                upload_dir = (DATA_DIR / "uploads").resolve()
                is_upload = source_path.parent == upload_dir
                still_referenced = any(
                    other.source_url == document.source_url
                    for other in self.documents.values()
                )
                if is_upload and not still_referenced and source_path.is_file():
                    source_path.unlink()
        version_ids = {
            version.id
            for version in self.versions.values()
            if version.id.startswith(f"{document_id}:v:")
        }
        for version_id in version_ids:
            self.versions.pop(version_id, None)
        asset_ids = {
            asset.id
            for asset in self.assets.values()
            if any(
                asset.external_id.startswith(f"{version_id}:")
                for version_id in version_ids
            )
        }
        for asset_id in asset_ids:
            asset = self.assets.pop(asset_id, None)
            if asset:
                asset_path = _safe_project_path(asset.local_path)
                if asset_path and asset_path.is_file():
                    asset_path.unlink()
        self.links = {
            (source, target)
            for source, target in self.links
            if source != document_id and target != document_id
        }
        self.role_links = {
            (source, target)
            for source, target in self.role_links
            if source != document_id
        }
        self.manual_links = {
            key: value
            for key, value in self.manual_links.items()
            if (
                value.source_document_id != document_id
                and value.target_document_id != document_id
            )
        }
        self.image_manifest = {
            key: value
            for key, value in self.image_manifest.items()
            if value.source_document_id != document_id
        }

    def clear_raw_paths(self) -> None:
        for document in self.documents.values():
            document.raw_local_path = ""

    def compact_versions(self) -> None:
        """Keep only the current version and its assets for every document."""
        current_ids: dict[str, str] = {}
        versions_by_document: dict[str, list[DocumentVersionRecord]] = {}
        for version in self.versions.values():
            document_id = version.id.split(":v:", 1)[0]
            versions_by_document.setdefault(document_id, []).append(version)
        for document_id, versions in versions_by_document.items():
            current = next((item for item in versions if item.is_current), None)
            current = current or max(versions, key=lambda item: item.version)
            current_ids[document_id] = current.id
            current.is_current = True
        self.versions = {
            version_id: version
            for version_id, version in self.versions.items()
            if version_id in current_ids.values()
        }
        kept_asset_ids = {
            asset.id
            for asset in self.assets.values()
            if any(
                asset.external_id.startswith(f"{version_id}:")
                for version_id in current_ids.values()
            )
        }
        removed_assets = [
            asset for asset in self.assets.values() if asset.id not in kept_asset_ids
        ]
        for asset in removed_assets:
            asset_path = _safe_project_path(asset.local_path)
            if asset_path and asset_path.is_file():
                asset_path.unlink()
        self.assets = {
            asset_id: asset
            for asset_id, asset in self.assets.items()
            if asset_id in kept_asset_ids
        }
        self.image_manifest = {
            key: manifest
            for key, manifest in self.image_manifest.items()
            if manifest.asset_id in kept_asset_ids or not manifest.asset_id
        }

    def write(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(
            self.data_dir / "Document.csv", DOCUMENT_COLUMNS, self.documents.values()
        )
        _write_csv(
            self.data_dir / "DocumentVersion.csv",
            VERSION_COLUMNS,
            self.versions.values(),
        )
        _write_csv(self.data_dir / "Asset.csv", ASSET_COLUMNS, self.assets.values())
        _write_csv(self.data_dir / "Role.csv", ROLE_COLUMNS, self.roles.values())
        _write_csv(
            self.data_dir / "manual_links.csv",
            MANUAL_LINK_COLUMNS,
            self.manual_links.values(),
        )
        _write_csv(
            self.data_dir / "image_manifest.csv",
            MANIFEST_COLUMNS,
            self.image_manifest.values(),
        )
        _write_csv(
            self.data_dir / "LINKS_TO.csv",
            ["from", "to"],
            ({"from": source, "to": target} for source, target in sorted(self.links)),
        )
        _write_csv(
            self.data_dir / "SourceType.csv",
            ["id", "code"],
            ({"id": code, "code": code} for code in ("local_file", "external_web")),
        )
        _write_csv(
            self.data_dir / "Status.csv",
            ["id", "name", "description"],
            (
                {
                    "id": "downloaded",
                    "name": "downloaded",
                    "description": "Документ успешно скачан",
                },
                {
                    "id": "manual_pending",
                    "name": "manual_pending",
                    "description": "Требуется ручная обработка",
                },
                {
                    "id": "error",
                    "name": "error",
                    "description": "Документ не удалось скачать",
                },
            ),
        )
        _write_csv(
            self.data_dir / "HAS_DOCUMENT.csv",
            ["from", "to"],
            (
                {"from": doc.source_type, "to": doc.id}
                for doc in sorted(self.documents.values(), key=lambda x: x.id)
            ),
        )
        _write_csv(
            self.data_dir / "HAS_VERSION.csv",
            ["from", "to"],
            (
                {"from": doc_id, "to": version.id}
                for version in sorted(self.versions.values(), key=lambda x: x.id)
                for doc_id in [version.id.split(":v:", 1)[0]]
            ),
        )
        _write_csv(
            self.data_dir / "HAS_STATUS.csv",
            ["from", "to"],
            (
                {
                    "from": version.id,
                    "to": self.documents[version.id.split(":v:", 1)[0]].status,
                }
                for version in sorted(self.versions.values(), key=lambda x: x.id)
                if version.is_current
            ),
        )
        _write_csv(
            self.data_dir / "HAS_ERROR.csv",
            ["from", "to"],
            (
                {"from": document.id, "to": "error"}
                for document in sorted(self.documents.values(), key=lambda x: x.id)
                if document.status == "error"
            ),
        )
        _write_csv(
            self.data_dir / "HAS_ASSET.csv",
            ["from", "to"],
            (
                {"from": version.id, "to": asset.id}
                for version in sorted(self.versions.values(), key=lambda x: x.id)
                for asset in sorted(self.assets.values(), key=lambda x: x.id)
                if asset.external_id.startswith(f"{version.id}:")
            ),
        )
        _write_csv(
            self.data_dir / "FOR_ROLE.csv",
            FOR_ROLE_COLUMNS,
            (
                {"from": document_id, "to": role_id}
                for document_id, role_id in sorted(self.role_links)
            ),
        )


def _write_csv(path: Path, fieldnames: list[str], records: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for record in records:
        if hasattr(record, "model_dump"):
            rows.append(record.model_dump())
        else:
            rows.append(dict(record))
    rows.sort(key=lambda row: tuple(str(row.get(column, "")) for column in fieldnames))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = {
                column: _csv_value(row.get(column, "")) for column in fieldnames
            }
            writer.writerow(normalized)


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def local_source_url(path: Path) -> str:
    return "file://" + path.relative_to(ROOT_DIR).as_posix()


def safe_name(value: str, fallback: str = "file") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value[:120] or fallback


def format_from_mime_or_url(mime_type: str, url: str) -> str:
    mime = mime_type.split(";", 1)[0].lower()
    if mime == "application/pdf" or urlparse(url).path.lower().endswith(".pdf"):
        return "pdf"
    if mime in {"text/markdown", "text/x-markdown"} or urlparse(
        url
    ).path.lower().endswith((".md", ".markdown")):
        return "markdown"
    if mime in {"text/html", "application/xhtml+xml"} or "notion.so" in (
        urlparse(url).hostname or ""
    ):
        return "html"
    return ""


def is_manual_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"t.me", "telegram.me", "telegram.dog"}


def is_image_url(url: str) -> bool:
    return Path(urlparse(url).path.lower()).suffix in IMAGE_EXTENSIONS


def extract_urls(text: str, base_url: str = "") -> list[str]:
    candidates = re.findall(r"https?://[^\s<>()\[\]\\\"']+", text)
    return [
        url
        for value in candidates
        if (url := canonical_url(value.rstrip(".,;"), base_url))
    ]


class PublicFetcher:
    """Fetch public documents and images without authentication."""

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "vue-graph-rag-mvp/1.0"})

    def fetch_document(self, url: str) -> DownloadedDocument:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadError("unsupported_scheme")
        if self._looks_like_auth_url(url):
            raise DownloadError("requires_authorization")
        request_url = f"https://r.jina.ai/{url}" if self._is_notion_url(url) else url
        response = self._get(request_url)
        if response.status_code in {401, 403}:
            raise DownloadError(f"http_{response.status_code}:private_or_forbidden")
        if response.status_code >= 400:
            raise DownloadError(f"http_{response.status_code}")
        if not response.content:
            raise DownloadError("empty_response")
        if self._is_notion_url(url):
            if len(response.content) <= 2048:
                raise DownloadError("notion_content_too_short")
            return DownloadedDocument(
                response.content,
                "markdown",
                "text/markdown",
                url,
                _title_from_jina(response.text),
            )
        final_url = canonical_url(response.url) or url
        if self._looks_like_auth_url(final_url):
            raise DownloadError("redirected_to_authorization")
        mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        content_format = format_from_mime_or_url(mime_type, final_url)
        if not content_format:
            raise DownloadError("unsupported_content_type")
        title = ""
        if content_format == "html":
            soup = BeautifulSoup(response.content, "html.parser")
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
        return DownloadedDocument(
            response.content, content_format, mime_type, final_url, title
        )

    def fetch_image(self, url: str) -> DownloadedDocument:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadError("unsupported_scheme")
        if self._looks_like_auth_url(url):
            raise DownloadError("requires_authorization")
        response = self._get(url)
        if response.status_code in {401, 403}:
            raise DownloadError(f"http_{response.status_code}:private_or_forbidden")
        if response.status_code >= 400:
            raise DownloadError(f"http_{response.status_code}")
        mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not mime_type.startswith("image/") and not is_image_url(response.url):
            raise DownloadError("not_an_image")
        if not response.content:
            raise DownloadError("empty_response")
        return DownloadedDocument(
            response.content, "image", mime_type, canonical_url(response.url) or url
        )

    def _get(self, url: str) -> requests.Response:
        current_url = url
        for _ in range(MAX_REDIRECTS + 1):
            self._validate_public_url(current_url)
            try:
                response = self.session.get(
                    current_url,
                    timeout=self.timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise DownloadError(f"request_error:{type(exc).__name__}") from exc
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise DownloadError("redirect_without_location")
                current_url = urljoin(current_url, location)
                continue
            try:
                content = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise DownloadError("response_too_large")
                response._content = bytes(content)
            finally:
                response.close()
            return response
        raise DownloadError("too_many_redirects")

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise DownloadError("unsupported_or_private_url")
        try:
            addresses = {
                info[4][0]
                for info in socket.getaddrinfo(
                    parsed.hostname, parsed.port, type=socket.SOCK_STREAM
                )
            }
        except socket.gaierror as exc:
            raise DownloadError("dns_error") from exc
        if any(
            ipaddress.ip_address(address).is_private
            or ipaddress.ip_address(address).is_loopback
            or ipaddress.ip_address(address).is_link_local
            or ipaddress.ip_address(address).is_multicast
            or ipaddress.ip_address(address).is_reserved
            or ipaddress.ip_address(address).is_unspecified
            for address in addresses
        ):
            raise DownloadError("private_url_blocked")

    @staticmethod
    def _is_notion_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host.endswith(".notion.site") or host == "notion.site"

    @staticmethod
    def _looks_like_auth_url(url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        return any(marker in path for marker in LOGIN_MARKERS) or host in {
            "accounts.google.com",
            "login.microsoftonline.com",
            "auth0.com",
        }


class DocumentParser:
    """Extract links/assets and render every document body as Markdown."""

    def parse(
        self, content: bytes, source_format: str, base_url: str = "", title: str = ""
    ) -> ParsedDocument:
        if source_format == "pdf":
            return self._parse_pdf(content, title)
        text = content.decode("utf-8", errors="replace")
        if source_format == "html":
            return self._parse_html(text, base_url, title)
        return self._parse_markdown(text, base_url, title)

    def to_markdown(
        self,
        content: bytes,
        source_format: str,
        title: str = "",
        parsed: ParsedDocument | None = None,
    ) -> bytes:
        if source_format == "markdown":
            text = content.decode("utf-8", errors="replace").strip()
        elif source_format == "html":
            soup = BeautifulSoup(content, "html.parser")
            for tag in soup.select("script, style, noscript, template"):
                tag.decompose()
            for image in soup.select("img[src], img[data-src]"):
                source = image.get("src") or image.get("data-src") or ""
                image.replace_with(
                    f"![{image.get('alt', '')}]({source})" if source else ""
                )
            for anchor in soup.select("a[href]"):
                href = anchor.get("href", "")
                label = anchor.get_text(" ", strip=True) or href
                anchor.replace_with(f"[{label}]({href})" if href else label)
            for heading in soup.find_all(re.compile("^h[1-6]$")):
                level = int(heading.name[1])
                heading.insert_before("\n" + "#" * level + " ")
                heading.insert_after("\n")
            for block in soup.find_all(
                ["p", "div", "section", "article", "li", "br", "tr"]
            ):
                block.insert_before("\n")
                block.insert_after("\n")
            text = soup.get_text(" ", strip=True)
            text = re.sub(r"[ \t]+\n", "\n", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
        elif source_format == "pdf":
            try:
                import fitz
            except ImportError as exc:
                raise ExporterError("PyMuPDF is required to process PDF files") from exc
            pdf = fitz.open(stream=content, filetype="pdf")
            pages = [page.get_text("text").strip() for page in pdf]
            body = "\n\n".join(page for page in pages if page)
            text = f"# {title or 'Untitled PDF'}\n\n{body}".strip()
            if parsed and parsed.links:
                text += "\n\n## Extracted links\n\n" + "\n".join(
                    f"- {url}" for url in parsed.links
                )
        else:
            raise ExporterError(f"unsupported source format: {source_format}")
        if (
            title
            and source_format in {"html", "markdown"}
            and not re.search(r"^#\s+", text, re.MULTILINE)
        ):
            text = f"# {title}\n\n{text}".strip()
        return (text.rstrip() + "\n").encode("utf-8")

    def _parse_pdf(self, content: bytes, title: str) -> ParsedDocument:
        try:
            import fitz
        except ImportError as exc:
            raise ExporterError("PyMuPDF is required to process PDF files") from exc
        pdf = fitz.open(stream=content, filetype="pdf")
        links: list[str] = []
        images: list[ImageCandidate] = []
        metadata_title = (pdf.metadata or {}).get("title") or title
        for page_index, page in enumerate(pdf):
            links.extend(
                link.get("uri", "") for link in page.get_links() if link.get("uri")
            )
            links.extend(extract_urls(page.get_text()))
            for image_index, image in enumerate(page.get_images(full=True)):
                xref = image[0]
                extracted = pdf.extract_image(xref)
                image_content = extracted.get("image", b"")
                if image_content:
                    extension = extracted.get("ext", "bin")
                    images.append(
                        ImageCandidate(
                            source_url=f"#page={page_index + 1}&xref={xref}",
                            position=page_index * 1000 + image_index,
                            content=image_content,
                            mime_type=f"image/{extension}"
                            if extension != "jpg"
                            else "image/jpeg",
                            external_id=f"embedded:page={page_index + 1}:xref={xref}",
                        )
                    )
        return ParsedDocument(
            metadata_title or "Untitled PDF", "pdf", _unique(links), images
        )

    def _parse_html(self, text: str, base_url: str, title: str) -> ParsedDocument:
        soup = BeautifulSoup(text, "html.parser")
        page_title = soup.title.get_text(" ", strip=True) if soup.title else title
        links = [anchor.get("href", "") for anchor in soup.select("a[href]")]
        links.extend(extract_urls(soup.get_text(" "), base_url))
        images: list[ImageCandidate] = []
        for position, tag in enumerate(
            soup.select("img[src], img[data-src], img[srcset]")
        ):
            sources = [tag.get("src") or tag.get("data-src") or ""]
            sources.extend(
                item.strip().split(" ", 1)[0]
                for item in tag.get("srcset", "").split(",")
                if item.strip()
            )
            for source in sources:
                normalized = canonical_url(source, base_url)
                if normalized:
                    images.append(
                        ImageCandidate(normalized, position, tag.get("alt", ""))
                    )
        links = [canonical_url(link, base_url) for link in links]
        return ParsedDocument(
            page_title or "Untitled HTML",
            "html",
            _unique(links),
            _unique_images(images),
        )

    def _parse_markdown(self, text: str, base_url: str, title: str) -> ParsedDocument:
        links = re.findall(r"(?<!!)(?:\[[^\]]*\])\(([^)\s]+)", text)
        links.extend(re.findall(r"<((?:https?://)[^>]+)>", text))
        links.extend(extract_urls(text, base_url))
        images: list[ImageCandidate] = []
        for position, match in enumerate(re.finditer(r"!\[([^]]*)\]\(([^)\s]+)", text)):
            source = canonical_url(match.group(2), base_url)
            if source:
                images.append(ImageCandidate(source, position, match.group(1)))
        inferred_title = title or next(
            (
                line.lstrip("# ").strip()
                for line in text.splitlines()
                if line.startswith("#")
            ),
            "Untitled Markdown",
        )
        links = [canonical_url(link, base_url) for link in links]
        return ParsedDocument(
            inferred_title, "markdown", _unique(links), _unique_images(images)
        )


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _unique_images(values: Iterable[ImageCandidate]) -> list[ImageCandidate]:
    seen: set[str] = set()
    result: list[ImageCandidate] = []
    for value in values:
        if value.source_url and value.source_url not in seen:
            result.append(value)
            seen.add(value.source_url)
    return result


class GraphBuilder:
    def __init__(self, store: CsvStore, fetcher: PublicFetcher, parser: DocumentParser):
        self.store = store
        self.fetcher = fetcher
        self.parser = parser

    def run(
        self,
        from_document_id: str | None = None,
        refresh: bool = False,
        compact_versions: bool = False,
        recreate_database: bool = False,
        update_database: bool = True,
        deduplicate: bool = False,
        root_url: str | None = None,
        roles: tuple[str, ...] = (),
        local_path: Path | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        predefined_document_ids: tuple[str, ...] = (),
        predefined_direction: str = "outgoing",
    ) -> None:
        if deduplicate:
            deduplicate_documents(self.store, ROOT_DIR)
        if local_path:
            document = self._ensure_local_document(local_path)
        elif root_url:
            document = self._ensure_external_document(root_url, refresh)
        elif from_document_id:
            document = self._existing_document(from_document_id)
        else:
            document = self._ensure_root_document()
        self._emit(
            on_event,
            "root_ready",
            document_id=document.id,
            title=document.title,
            url=document.source_url,
        )
        for role in roles:
            self.store.put_role(document.id, role)
        for target_id in predefined_document_ids:
            if target_id == document.id or target_id not in self.store.documents:
                continue
            edge = (document.id, target_id) if predefined_direction == "outgoing" else (target_id, document.id)
            self.store.links.add(edge)
            self._emit(on_event, "link_created", source_document_id=edge[0], target_document_id=edge[1], status="predefined")
        queue: deque[tuple[DocumentRecord, int]] = deque([(document, 0)])
        processed: set[str] = set()
        while queue:
            current, depth = queue.popleft()
            if current.id in processed:
                continue
            processed.add(current.id)
            if not current.is_active or current.status == "error":
                self._emit(
                    on_event,
                    "document_skipped",
                    document_id=current.id,
                    url=current.source_url,
                    reason=current.status or "inactive",
                )
                continue
            self._emit(
                on_event,
                "document_started",
                document_id=current.id,
                title=current.title,
                url=current.source_url,
                depth=depth,
            )
            try:
                parsed = self._process_document(current, refresh)
            except Exception as exc:
                self._emit(
                    on_event,
                    "error",
                    document_id=current.id,
                    url=current.source_url,
                    reason=f"{type(exc).__name__}: {exc}",
                )
                raise
            self._emit(
                on_event,
                "document_processed",
                document_id=current.id,
                title=current.title,
                url=current.source_url,
                depth=depth,
                links_found=len(parsed.links),
                images_found=len(parsed.images),
            )
            if depth >= 1:
                continue
            for target_url in parsed.links:
                self._emit(
                    on_event,
                    "link_found",
                    source_document_id=current.id,
                    source_title=current.title,
                    url=target_url,
                    depth=depth + 1,
                )
                if is_image_url(target_url):
                    continue
                target_id = self.store.find_document_id(
                    target_url
                ) or document_id_for_url(target_url)
                existing = self.store.documents.get(target_id)
                if is_manual_url(target_url):
                    if existing:
                        self.store.remove_document(target_id)
                    self.store.put_manual_link(
                        ManualLinkRecord(
                            source_document_id=current.id,
                            target_document_id=target_id,
                            target_external_id=external_identity(target_url),
                            source_url=current.source_url,
                            target_url=target_url,
                            discovered_depth=depth + 1,
                            reason="telegram_requires_authorization",
                        )
                    )
                    self._emit(
                        on_event,
                        "manual_link",
                        source_document_id=current.id,
                        url=target_url,
                        status="manual",
                        reason="telegram_requires_authorization",
                    )
                    continue
                cached_path = (
                    _safe_project_path(existing.local_path) if existing else None
                )
                try:
                    is_cached = (
                        existing
                        and cached_path
                        and cached_path.exists()
                        and not refresh
                    )
                    if is_cached:
                        self._emit(
                            on_event,
                            "duplicate",
                            source_document_id=current.id,
                            url=target_url,
                            document_id=target_id,
                            status="cached",
                        )
                        target = existing
                    else:
                        target = self._download_and_register(
                            target_url, target_id, existing
                        )
                        self._emit(
                            on_event,
                            "document_downloaded",
                            source_document_id=current.id,
                            url=target_url,
                            document_id=target.id,
                            status="downloaded",
                        )
                except DownloadError as exc:
                    if existing and exc.reason == "notion_content_too_short":
                        self.store.remove_document(target_id)
                    self._register_failed_document(target_id, target_url, exc.reason)
                    self.store.put_manual_link(
                        ManualLinkRecord(
                            source_document_id=current.id,
                            target_document_id=target_id,
                            target_external_id=external_identity(target_url),
                            source_url=current.source_url,
                            target_url=target_url,
                            discovered_depth=depth + 1,
                            reason=exc.reason,
                        )
                    )
                    self._emit(
                        on_event,
                        "manual_link",
                        source_document_id=current.id,
                        url=target_url,
                        status="manual",
                        reason=exc.reason,
                    )
                    continue
                self.store.manual_links.pop(
                    (current.id, external_identity(target_url)), None
                )
                self.store.links.add((current.id, target.id))
                self._emit(
                    on_event,
                    "link_created",
                    source_document_id=current.id,
                    target_document_id=target.id,
                    url=target_url,
                    status="linked",
                )
                for role in roles:
                    self.store.put_role(target.id, role)
                queue.append((target, depth + 1))
        if compact_versions:
            self.store.compact_versions()
        self.store.clear_raw_paths()
        self.store.write()
        if update_database:
            graph_loader = GraphLoader(self.store.data_dir, DATABASE_PATH)
            if recreate_database:
                graph_loader.recreate()
            else:
                graph_loader.sync()
        if RAW_DIR.exists():
            shutil.rmtree(RAW_DIR)
        self._emit(
            on_event,
            "finished",
            documents=len(self.store.documents),
            links=len(self.store.links),
            manual_links=len(self.store.manual_links),
        )

    @staticmethod
    def _emit(
        callback: Callable[[dict[str, Any]], None] | None,
        event: str,
        **payload: Any,
    ) -> None:
        if callback:
            callback({"event": event, **payload})

    def _register_failed_document(self, document_id: str, url: str, reason: str) -> DocumentRecord:
        existing = self.store.documents.get(document_id)
        if existing:
            existing.status = "error"
            existing.is_active = False
            return existing
        document = DocumentRecord(
            id=document_id, title=url, source_url=url,
            external_id=external_identity(url), source_type="external_web",
            sha256="", local_path="", is_active=False, status="error",
        )
        self.store.put_document(document)
        current_version = next(
            (version for version in self.store.versions.values()
             if version.id.startswith(f"{document_id}:v:") and version.is_current),
            None,
        )
        if current_version:
            current_version.error = reason
        else:
            self.store.put_version(DocumentVersionRecord(
                id=f"{document_id}:v:error", version=1, sha256="",
                local_path="", created_at=datetime.now(UTC).isoformat(),
                is_current=True, error=reason,
            ))
        return document

    def _existing_document(self, document_id: str) -> DocumentRecord:
        document = self.store.documents.get(document_id)
        if not document:
            raise ExporterError(
                f"Document not found in {self.store.data_dir / 'Document.csv'}: "
                f"{document_id}"
            )
        return document

    def _ensure_external_document(self, url: str, refresh: bool) -> DocumentRecord:
        normalized_url = canonical_url(url)
        if not normalized_url:
            raise ExporterError(f"Invalid root URL: {url}")
        document_id = self.store.find_document_id(
            normalized_url
        ) or document_id_for_url(normalized_url)
        existing = self.store.documents.get(document_id)
        cached_path = (
            _safe_project_path(existing.local_path) if existing else None
        )
        if existing and cached_path and cached_path.exists() and not refresh:
            return existing
        return self._download_and_register(normalized_url, document_id, existing)

    def _ensure_local_document(self, source_path: Path) -> DocumentRecord:
        source_path = source_path.resolve()
        if not source_path.exists() or not source_path.is_file():
            raise ExporterError(f"Uploaded file not found: {source_path}")
        source_format = format_from_mime_or_url("", source_path.name)
        if not source_format:
            raise ExporterError(
                "Unsupported uploaded file format; use PDF, HTML, or Markdown"
            )
        raw = source_path.read_bytes()
        document_id = document_identity(
            "upload:" + sha256_bytes(raw) + ":" + source_path.suffix.lower()
        )
        existing = self.store.documents.get(document_id)
        if existing and (ROOT_DIR / existing.local_path).exists():
            return existing
        parsed = self.parser.parse(
            raw, source_format, local_source_url(source_path), source_path.stem
        )
        markdown = self.parser.to_markdown(raw, source_format, parsed.title, parsed)
        md_path = self._save_markdown(markdown, document_id)
        raw_path = self._save_raw(raw, document_id, source_path.suffix.lower())
        document = DocumentRecord(
            id=document_id,
            title=parsed.title or source_path.stem,
            source_url=local_source_url(source_path),
            external_id="upload:" + sha256_bytes(raw),
            source_type="local_file",
            sha256=sha256_bytes(markdown),
            local_path=md_path,
            content_format="markdown",
            source_format=source_format,
            source_name=source_path.name,
            raw_local_path=raw_path,
        )
        self.store.put_document(document)
        self._register_version(document, document.sha256)
        return document

    def _ensure_root_document(self) -> DocumentRecord:
        if not ROOT_DOCUMENT_PATH.exists():
            raise ExporterError(f"Root document not found: {ROOT_DOCUMENT_PATH}")
        raw = ROOT_DOCUMENT_PATH.read_bytes()
        document_id = document_identity(
            "local:" + ROOT_DOCUMENT_PATH.relative_to(ROOT_DIR).as_posix()
        )
        parsed = self.parser.parse(
            raw, "pdf", local_source_url(ROOT_DOCUMENT_PATH), ROOT_DOCUMENT_PATH.stem
        )
        markdown = self.parser.to_markdown(raw, "pdf", parsed.title, parsed)
        md_path = self._save_markdown(markdown, document_id)
        raw_path = self._save_raw(raw, document_id, ".pdf")
        document = DocumentRecord(
            id=document_id,
            title=parsed.title,
            source_url=local_source_url(ROOT_DOCUMENT_PATH),
            external_id="local:" + ROOT_DOCUMENT_PATH.relative_to(ROOT_DIR).as_posix(),
            source_type="local_file",
            sha256=sha256_bytes(markdown),
            local_path=md_path,
            content_format="markdown",
            source_format="pdf",
            source_name=ROOT_DOCUMENT_PATH.name,
            raw_local_path=raw_path,
        )
        self.store.put_document(document)
        self._register_version(document, document.sha256)
        return document

    def _process_document(
        self, document: DocumentRecord, refresh: bool
    ) -> ParsedDocument:
        raw, source_format = self._read_source(document)
        parsed = self.parser.parse(
            raw, source_format, document.source_url, document.title
        )
        version = next(
            (
                item
                for item in self.store.versions.values()
                if item.id == f"{document.id}:v:{document.sha256}"
            ),
            None,
        )
        if version is None:
            version = self._register_version(document, document.sha256)
        self._register_images(document, version, parsed.images, refresh)
        return parsed

    def _download_and_register(
        self, url: str, document_id: str, existing: DocumentRecord | None
    ) -> DocumentRecord:
        downloaded = self.fetcher.fetch_document(url)
        parsed = self.parser.parse(
            downloaded.content,
            downloaded.content_format,
            downloaded.final_url,
            downloaded.title,
        )
        markdown = self.parser.to_markdown(
            downloaded.content, downloaded.content_format, parsed.title, parsed
        )
        md_path = self._save_markdown(markdown, document_id)
        raw_path = self._save_raw(
            downloaded.content,
            document_id,
            _suffix_for_format(downloaded.content_format, downloaded.mime_type, url),
        )
        document = DocumentRecord(
            id=document_id,
            title=parsed.title
            or downloaded.title
            or (existing.title if existing else _title_from_url(url)),
            source_url=downloaded.final_url,
            external_id=external_identity(downloaded.final_url),
            source_type="external_web",
            sha256=sha256_bytes(markdown),
            local_path=md_path,
            content_format="markdown",
            source_format=downloaded.content_format,
            raw_local_path=raw_path,
        )
        self.store.put_document(document)
        self._register_version(document, document.sha256)
        return document

    def _read_source(self, document: DocumentRecord) -> tuple[bytes, str]:
        candidates = []
        if raw_path := _safe_project_path(document.raw_local_path):
            candidates.append((raw_path, document.source_format))
        if local_path := _safe_project_path(document.local_path):
            candidates.append(
                (
                    local_path,
                    "markdown"
                    if local_path.suffix.lower() == ".md"
                    else document.source_format,
                )
            )
        for source_path, source_format in candidates:
            if source_path.exists():
                return source_path.read_bytes(), source_format
        if document.source_url.startswith("file://"):
            source_path = ROOT_DIR / document.source_url.removeprefix("file://")
            if source_path.exists():
                return source_path.read_bytes(), document.source_format
        downloaded = self.fetcher.fetch_document(document.source_url)
        parsed = self.parser.parse(
            downloaded.content,
            downloaded.content_format,
            downloaded.final_url,
            downloaded.title,
        )
        markdown = self.parser.to_markdown(
            downloaded.content, downloaded.content_format, parsed.title, parsed
        )
        document.local_path = self._save_markdown(markdown, document.id)
        document.raw_local_path = self._save_raw(
            downloaded.content,
            document.id,
            _suffix_for_format(
                downloaded.content_format, downloaded.mime_type, document.source_url
            ),
        )
        document.source_format = downloaded.content_format
        document.content_format = "markdown"
        document.status = "downloaded"
        document.is_active = True
        document.sha256 = sha256_bytes(markdown)
        self.store.put_document(document)
        version = self._register_version(document, document.sha256)
        version.error = ""
        return downloaded.content, downloaded.content_format

    def _register_version(
        self, document: DocumentRecord, digest: str
    ) -> DocumentVersionRecord:
        prefix = f"{document.id}:v:"
        existing = self.store.versions.get(prefix + digest)
        if existing:
            for version in self.store.versions.values():
                if version.id.startswith(prefix):
                    version.is_current = version.id == existing.id
            existing.local_path = document.local_path
            return existing
        version_number = (
            max(
                (
                    v.version
                    for v in self.store.versions.values()
                    if v.id.startswith(prefix)
                ),
                default=0,
            )
            + 1
        )
        for version in self.store.versions.values():
            if version.id.startswith(prefix):
                version.is_current = False
        record = DocumentVersionRecord(
            id=prefix + digest,
            version=version_number,
            sha256=digest,
            local_path=document.local_path,
            created_at=datetime.now(UTC).isoformat(),
            is_current=True,
        )
        self.store.put_version(record)
        return record

    def _register_images(
        self,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        candidates: list[ImageCandidate],
        refresh: bool,
    ) -> None:
        for candidate in candidates:
            manifest = self.store.image_manifest.get(
                (document.id, candidate.source_url)
            )
            if manifest and not refresh:
                continue
            try:
                if candidate.content is not None:
                    self._register_asset(
                        document,
                        version,
                        candidate,
                        candidate.content,
                        candidate.mime_type,
                    )
                else:
                    downloaded = self.fetcher.fetch_image(candidate.source_url)
                    self._register_asset(
                        document,
                        version,
                        candidate,
                        downloaded.content,
                        downloaded.mime_type,
                    )
            except DownloadError as exc:
                self.store.put_manifest(
                    ImageManifestRecord(
                        asset_id="",
                        source_document_id=document.id,
                        source_url=candidate.source_url,
                        status="manual_pending",
                        reason=exc.reason,
                    )
                )

    def _register_asset(
        self,
        document: DocumentRecord,
        version: DocumentVersionRecord,
        candidate: ImageCandidate,
        content: bytes,
        mime_type: str = "",
    ) -> None:
        digest = sha256_bytes(content)
        source_identity = (
            candidate.external_id or f"image:{canonical_url(candidate.source_url)}"
        )
        asset_external_id = f"{version.id}:{source_identity}"
        asset_id = (
            "asset-"
            + hashlib.sha256(asset_external_id.encode("utf-8")).hexdigest()[:24]
        )
        extension = _suffix_for_image(
            mime_type or candidate.mime_type, candidate.source_url
        )
        target = IMAGES_DIR / f"{asset_id}{extension}"
        if not target.exists() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        local_path = target.relative_to(ROOT_DIR).as_posix()
        self.store.put_asset(
            AssetRecord(
                id=asset_id,
                mime_type=mime_type
                or candidate.mime_type
                or "application/octet-stream",
                source_url=candidate.source_url,
                local_path=local_path,
                external_id=asset_external_id,
                sha256=digest,
                position=candidate.position,
                caption=candidate.caption,
            )
        )
        self.store.put_manifest(
            ImageManifestRecord(
                asset_id=asset_id,
                source_document_id=document.id,
                source_url=candidate.source_url,
                local_path=local_path,
                mime_type=mime_type or candidate.mime_type,
                sha256=digest,
                status="downloaded",
            )
        )

    def _save_markdown(self, content: bytes, document_id: str) -> str:
        target = DOCUMENTS_DIR / f"{document_id}.md"
        if not target.exists() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return target.relative_to(ROOT_DIR).as_posix()

    def _save_raw(self, content: bytes, document_id: str, suffix: str) -> str:
        target = RAW_DIR / f"{document_id}{suffix}"
        if not target.exists() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return target.relative_to(ROOT_DIR).as_posix()


def _suffix_for_format(content_format: str, mime_type: str, hint: str) -> str:
    suffix = Path(urlparse(hint).path).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return suffix
    if content_format == "pdf":
        return ".pdf"
    if content_format == "markdown":
        return ".md"
    if content_format == "html":
        return ".html"
    return mimetypes.guess_extension(mime_type) or ".bin"


def _suffix_for_image(mime_type: str, hint: str) -> str:
    suffix = Path(urlparse(hint).path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix
    return mimetypes.guess_extension(mime_type) or ".bin"


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return (
        Path(path).stem.replace("-", " ").replace("_", " ").strip()
        or urlparse(url).hostname
        or "Untitled document"
    )


def _title_from_jina(text: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--from-document", help="Start a depth-1 traversal from an existing Document id"
    )
    source_group.add_argument(
        "--root-url", help="Start a depth-1 traversal from a public document URL"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download existing documents and images instead of using local cache",
    )
    parser.add_argument(
        "--compact-versions",
        action="store_true",
        help="Keep only the current version of each document and its assets",
    )
    parser.add_argument(
        "--role",
        nargs="+",
        choices=("student", "support"),
        default=(),
        help="Assign selected roles to the root document and its discovered files",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Merge existing records with the same normalized external URL",
    )
    parser.add_argument(
        "--recreate-database",
        action="store_true",
        help="Destructively recreate LadybugDB (does not preserve extra tables)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    try:
        for directory in (DATA_DIR, DOCUMENTS_DIR, RAW_DIR, IMAGES_DIR):
            directory.mkdir(parents=True, exist_ok=True)
        GraphBuilder(CsvStore(DATA_DIR), PublicFetcher(), DocumentParser()).run(
            args.from_document,
            refresh=args.refresh,
            compact_versions=args.compact_versions,
            recreate_database=args.recreate_database,
            deduplicate=args.deduplicate,
            root_url=args.root_url,
            roles=tuple(args.role),
        )
    except (ExporterError, DatabaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        traceback.print_exc()
        print(
            f"error: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 1
    print(f"CSV export and LadybugDB synchronization completed in {DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
