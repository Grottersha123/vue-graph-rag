from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from mvp_v2.application.dto.importing import LegacyImportResult, MediaCopyPlan
from mvp_v2.application.manifests import DumpManifest
from mvp_v2.domain.snapshot import GraphSnapshot
from mvp_v2.ports.media import MediaResolver


class LegacyImporter(Protocol):
    def preview(self, source_root: Path) -> LegacyImportResult:
        """Read legacy inputs and return all deterministic findings without writes."""


class MediaAuthority(Protocol):
    def copy(
        self, *, source_root: Path, destination: Path, plan: MediaCopyPlan
    ) -> MediaResolver:
        """Publish a new verified authority without changing its source."""


class DumpRepository(Protocol):
    def publish(
        self, *, dumps_root: Path, snapshot: GraphSnapshot, published_at: datetime
    ) -> DumpManifest:
        """Publish one immutable, validated numbered dump."""
