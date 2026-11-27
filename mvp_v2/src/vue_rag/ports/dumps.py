from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from vue_rag.application.dump_lifecycle.manifests import DumpManifest
from vue_rag.domain.snapshot import GraphSnapshot


class DumpRepository(Protocol):
    def publish(
        self,
        *,
        dumps_root: Path,
        snapshot: GraphSnapshot,
        published_at: datetime,
    ) -> DumpManifest:
        """Publish one immutable, validated numbered dump."""
