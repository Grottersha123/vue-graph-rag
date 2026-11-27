from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import redis

from vue_rag.adapters.ladybug import LadybugAnn, LadybugEvidenceHydrator
from vue_rag.adapters.ollama import LocalHttpTransport, OllamaAdapter
from vue_rag.adapters.selora import HttpxSeloraTransport, SeloraAdapter
from vue_rag.application.dump_lifecycle.manifests import (
    DATABASE_FORMAT,
    DatabaseManifest,
)
from vue_rag.application.json_encoding import canonical_json_bytes
from vue_rag.application.rag.agent.chat import ChatService
from vue_rag.application.rag.agent.prompts import PROMPT_TEMPLATES
from vue_rag.application.rag.providers import (
    FallbackGenerationProvider,
    NamedGenerationProvider,
)
from vue_rag.application.rag.retrieval.retrieval import (
    SearchExecutionLock,
    SearchService,
)
from vue_rag.backend.audit import (
    GatewayAudit,
    PromptTraceBinder,
    TracingGenerationModel,
)
from vue_rag.backend.config import GatewaySettings
from vue_rag.backend.contracts import HealthHttpResponse
from vue_rag.backend.profiles import ProfileRegistry, RedisProfileStore
from vue_rag.backend.service import CapacityLimiter, GatewayChatService
from vue_rag.domain.schema_registry import (
    NODE_TABLE_NAMES,
    RELATION_TABLE_NAMES,
    SCHEMA_PROFILE_SHA256,
)
from vue_rag.ports.models import GenerationProvider


class ReadinessProbe(Protocol):
    def check(self) -> HealthHttpResponse: ...


class DatabaseProbe(Protocol):
    def ready(self) -> bool: ...


class LadybugDatabaseProbe:
    def __init__(self, connection: Any, lock: SearchExecutionLock) -> None:
        self._connection = connection
        self._lock = lock

    def ready(self) -> bool:
        self._lock.acquire()
        try:
            result = self._connection.execute("RETURN 1")
            return bool(result.has_next() and result.get_next() == [1])
        except Exception:
            return False
        finally:
            self._lock.release()


def load_database_manifest(
    artifact: Path, expected_database_name: str
) -> DatabaseManifest:
    if artifact.is_symlink() or not artifact.is_dir() or artifact.suffix != ".lbdb":
        raise ValueError("database artifact must be a real .lbdb directory")
    manifest_path = artifact / "_mvp_v2_manifest.json"
    database_path = artifact / "database.lbug"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("database manifest is missing")
    if database_path.is_symlink() or not database_path.is_file():
        raise ValueError("database file is missing")
    raw = manifest_path.read_bytes()
    manifest = DatabaseManifest.model_validate_json(raw)
    if manifest.format != DATABASE_FORMAT:
        raise ValueError("database format is unsupported")
    if manifest.schema_profile_sha256 != SCHEMA_PROFILE_SHA256:
        raise ValueError("database schema profile is incompatible")
    if manifest.database_name != expected_database_name:
        raise ValueError("database manifest name does not match configuration")
    if canonical_json_bytes(manifest.canonical_payload()) != raw:
        raise ValueError("database manifest is not canonical")
    return manifest


def _query_rows(connection: Any, query: str) -> list[list[object]]:
    result = connection.execute(query)
    rows: list[list[object]] = []
    while result.has_next():
        rows.append(list(result.get_next()))
    return rows


def validate_database_contents(connection: Any, manifest: DatabaseManifest) -> None:
    expected_counts = {item.table: item.count for item in manifest.row_counts}
    try:
        for table in NODE_TABLE_NAMES:
            rows = _query_rows(connection, f"MATCH (n:{table}) RETURN count(n)")
            if rows != [[expected_counts[table]]]:
                raise ValueError("database row counts do not match manifest")
        for table in RELATION_TABLE_NAMES:
            rows = _query_rows(connection, f"MATCH ()-[r:{table}]->() RETURN count(r)")
            if rows != [[expected_counts[table]]]:
                raise ValueError("database row counts do not match manifest")
        embedding_count = expected_counts["Embedding"]
        identities = _query_rows(
            connection,
            "MATCH (e:Embedding) RETURN DISTINCT e.provider, e.model, "
            "e.model_version, e.dimension",
        )
        expected_identity = [
            manifest.embedding_space.provider,
            manifest.embedding_space.model,
            manifest.embedding_space.model_version,
            manifest.embedding_space.dimension,
        ]
        if (embedding_count == 0 and identities) or (
            embedding_count > 0 and identities != [expected_identity]
        ):
            raise ValueError("database embedding identity does not match manifest")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("database artifact validation failed") from error


class RuntimeReadiness:
    def __init__(
        self,
        *,
        ollama_base_url: str,
        database: DatabaseProbe,
        profiles: RedisProfileStore,
        audit: GatewayAudit,
    ) -> None:
        self._ollama_base_url = ollama_base_url
        self._database = database
        self._profiles = profiles
        self._audit = audit

    def check(self) -> HealthHttpResponse:
        ollama_ready = False
        try:
            with httpx.Client(timeout=2.0, trust_env=False) as client:
                response = client.get(f"{self._ollama_base_url.rstrip('/')}/api/tags")
                ollama_ready = response.is_success
        except httpx.HTTPError:
            ollama_ready = False
        database_ready = self._database.ready()
        redis_ready = self._profiles.ready()
        audit_ready = self._audit.ready()
        ready = database_ready and ollama_ready and redis_ready and audit_ready
        return HealthHttpResponse(
            status="ready" if ready else "not_ready",
            database=database_ready,
            ollama=ollama_ready,
            redis=redis_ready,
            audit=audit_ready,
        )


@dataclass(slots=True)
class GatewayRuntime:
    gateway: GatewayChatService
    readiness: ReadinessProbe
    _connection: Any
    _database: Any
    _redis: redis.Redis

    def close(self) -> None:
        self._connection.close()
        self._database.close()
        self._redis.close()


def build_runtime(settings: GatewaySettings) -> GatewayRuntime:
    root = settings.databases_root
    if root.is_symlink() or not root.is_dir():
        raise ValueError("database root must be a real directory")
    artifact = settings.database_path
    if artifact.parent != root:
        raise ValueError("database artifact must be a direct child of database root")
    manifest = load_database_manifest(artifact, settings.database_name)
    audit = GatewayAudit(settings.audit_directory)
    audit.prepare()
    redis_client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )
    profile_store = RedisProfileStore(redis_client)
    adapter = OllamaAdapter(
        LocalHttpTransport(
            settings.ollama_base_url,
            timeout_seconds=settings.ollama_timeout_seconds,
            allowed_lan_hosts=settings.ollama_allowed_lan_hosts,
        )
    )
    fallback_generation = NamedGenerationProvider(adapter, "gemma")
    generation: GenerationProvider = fallback_generation
    if (
        settings.selora_url is not None
        and settings.selora_api_key is not None
        and settings.selora_model is not None
    ):
        primary_generation = NamedGenerationProvider(
            SeloraAdapter(
                HttpxSeloraTransport(
                    settings.selora_url,
                    settings.selora_api_key,
                ),
                settings.selora_model,
            ),
            "selora",
        )
        generation = FallbackGenerationProvider(primary_generation, fallback_generation)
    traced_generation = TracingGenerationModel(generation, audit.prompt_writer)
    ladybug_home = Path(
        os.environ.get("MVP_V2_LADYBUG_HOME", "/tmp/mvp-v2-ladybug-home")
    )
    ladybug_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ladybug_home.is_symlink() or not ladybug_home.is_dir():
        raise ValueError("MVP_V2_LADYBUG_HOME must be a real directory")
    os.environ["HOME"] = str(ladybug_home.resolve(strict=True))
    ladybug = importlib.import_module("ladybug")
    database = ladybug.Database(str(artifact / "database.lbug"), read_only=True)
    connection = ladybug.Connection(database)
    validate_database_contents(connection, manifest)
    search_lock = threading.RLock()
    database_probe = LadybugDatabaseProbe(connection, search_lock)
    search = SearchService(
        adapter,
        LadybugAnn(connection, manifest),
        manifest,
        evidence_hydrator=LadybugEvidenceHydrator(connection),
        execution_lock=search_lock,
    )
    profile_registry = ProfileRegistry()
    chats = {
        profile_id: ChatService(
            search,
            traced_generation,
            max_evidence_items=settings.evidence_limit,
            prompt_template=template,
        )
        for profile_id, template in PROMPT_TEMPLATES.items()
    }
    gateway = GatewayChatService(
        chats=chats,
        manifest=manifest,
        profiles=profile_registry,
        profile_store=profile_store,
        audit=audit,
        trace_binder=PromptTraceBinder(),
        capacity=CapacityLimiter(
            settings.total_concurrency,
            {settings.chat_model: settings.model_concurrency},
        ),
        allowed_user_ids=settings.allowed_user_ids,
        chat_model=settings.chat_model,
        chat_model_version=settings.chat_model_version,
        evidence_limit=settings.evidence_limit,
        minimum_score=settings.minimum_score,
    )
    readiness = RuntimeReadiness(
        ollama_base_url=settings.ollama_base_url,
        database=database_probe,
        profiles=profile_store,
        audit=audit,
    )
    return GatewayRuntime(
        gateway=gateway,
        readiness=readiness,
        _connection=connection,
        _database=database,
        _redis=redis_client,
    )
