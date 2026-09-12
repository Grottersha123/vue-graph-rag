"""Document identity and duplicate-merging utilities."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse


def canonical_url(value: str, base_url: str = "") -> str:
    absolute = urljoin(base_url, value.strip())
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return ""
    port = parsed.port
    netloc = hostname
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    if (
        hostname == "docs.google.com"
        or hostname.endswith(".google.com")
        and (hostname.startswith("docs.") or hostname.startswith("sheets."))
        or hostname.endswith(".notion.site")
        or hostname in {"notion.site", "app.notion.com"}
    ):
        query = ""
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "/", "", query, ""))


def external_identity(url: str) -> str:
    normalized = canonical_url(url)
    parsed = urlparse(normalized)
    host = (parsed.hostname or "").lower()
    if host.endswith(".notion.site") or host in {"notion.site", "app.notion.com"}:
        match = re.search(r"([0-9a-f]{32})", parsed.path.replace("-", ""), re.IGNORECASE)
        if match:
            return "notion:" + match.group(1).lower()
    return "web:" + normalized


def document_identity(external_id: str) -> str:
    return "doc-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:24]


def document_id_for_url(url: str) -> str:
    return document_identity(external_identity(url))


def deduplicate_documents(store: Any, root_dir: Path) -> None:
    """Merge external documents sharing the same normalized identity."""
    groups: dict[str, list[str]] = defaultdict(list)
    for document in store.documents.values():
        if document.source_type == "external_web":
            groups[external_identity(document.source_url)].append(document.id)
    aliases: dict[str, str] = {}
    for document_ids in groups.values():
        if len(document_ids) > 1:
            keeper = sorted(document_ids)[0]
            aliases.update({document_id: keeper for document_id in document_ids if document_id != keeper})
    if not aliases:
        return
    removed_version_ids = {version.id for document_id in aliases for version in store.versions.values() if version.id.startswith(f"{document_id}:v:")}
    removed_asset_ids = {asset.id for asset in store.assets.values() if any(asset.external_id.startswith(f"{version_id}:") for version_id in removed_version_ids)}
    for asset in store.assets.values():
        if asset.id in removed_asset_ids:
            asset_path = root_dir / asset.local_path
            if asset_path.exists():
                asset_path.unlink()
    store.documents = {k: v for k, v in store.documents.items() if k not in aliases}
    store.versions = {k: v for k, v in store.versions.items() if k not in removed_version_ids}
    store.assets = {k: v for k, v in store.assets.items() if k not in removed_asset_ids}
    store.links = {(aliases.get(source, source), aliases.get(target, target)) for source, target in store.links}
    store.role_links = {(aliases.get(source, source), role_id) for source, role_id in store.role_links}
    store.manual_links = {(aliases.get(r.source_document_id, r.source_document_id), r.target_external_id): r.model_copy(update={"source_document_id": aliases.get(r.source_document_id, r.source_document_id), "target_document_id": aliases.get(r.target_document_id, r.target_document_id)}) for r in store.manual_links.values()}
    store.image_manifest = {(aliases.get(r.source_document_id, r.source_document_id), r.source_url): r.model_copy(update={"source_document_id": aliases.get(r.source_document_id, r.source_document_id)}) for r in store.image_manifest.values() if r.asset_id not in removed_asset_ids}
