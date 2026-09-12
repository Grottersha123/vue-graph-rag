"""Local Streamlit UI for importing and maintaining the MVP document graph."""

from __future__ import annotations

import csv
import hashlib
import logging
import re
import shutil
import sys
import tempfile
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from logging.handlers import RotatingFileHandler

import streamlit as st

MVP_DIR = Path(__file__).resolve().parent
ROOT_DIR = MVP_DIR.parent
DATA_DIR = MVP_DIR / "mvp_data"
DATABASE_PATH = DATA_DIR / "vue_rag.lbdb"
UPLOADS_DIR = DATA_DIR / "uploads"
SCRIPTS_DIR = MVP_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from database import GraphLoader  # noqa: E402
from vue_rag_exporter import (  # noqa: E402
    CsvStore,
    DocumentParser,
    GraphBuilder,
    PublicFetcher,
    _safe_project_path,
)

ALLOWED_SUFFIXES = {".pdf", ".md", ".markdown", ".html", ".htm"}
LOG_PATH = DATA_DIR / "streamlit_app.log"


def configure_logging() -> logging.Logger:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("vue_graph_rag.streamlit")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
    return logger


logger = configure_logging()


@contextmanager
def mutation_lock() -> Iterator[None]:
    """Serialize CSV/DB mutations in the local single-user app."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / ".mutation.lock"
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            import fcntl
        except ImportError:
            yield
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def mutation_snapshot() -> Iterator[None]:
    """Restore CSV and LadybugDB if an import/delete mutation fails."""
    backup_dir = Path(tempfile.mkdtemp(prefix="snapshot-", dir=DATA_DIR))
    paths = list(DATA_DIR.glob("*.csv"))
    for database_path in (DATABASE_PATH, Path(str(DATABASE_PATH) + ".wal")):
        if database_path.exists():
            paths.append(database_path)
    managed_dirs = [
        DATA_DIR / "documents",
        DATA_DIR / "images",
        DATA_DIR / "raw",
        DATA_DIR / "uploads",
    ]
    try:
        for source in paths:
            shutil.copy2(source, backup_dir / source.name)
        for source in managed_dirs:
            if source.exists():
                shutil.copytree(source, backup_dir / source.name)
        yield
    except Exception:
        for current in (
            list(DATA_DIR.glob("*.csv"))
            + [
                DATABASE_PATH,
                Path(str(DATABASE_PATH) + ".wal"),
            ]
            + managed_dirs
        ):
            if current.is_dir():
                shutil.rmtree(current)
            elif current.exists():
                current.unlink()
        for backup in backup_dir.iterdir():
            if backup.is_dir():
                shutil.copytree(backup, DATA_DIR / backup.name)
            else:
                shutil.copy2(backup, DATA_DIR / backup.name)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def read_rows(filename: str) -> list[dict[str, str]]:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def upload_path(uploaded_file: Any) -> Path:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Поддерживаются только PDF, Markdown и HTML")
    content = uploaded_file.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    stem = re.sub(r"[^\w.-]+", "_", Path(uploaded_file.name).stem).strip("._") or "file"
    target = UPLOADS_DIR / f"{digest}-{stem[:60]}{suffix}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return target


@dataclass
class ProgressState:
    """Bounded, incremental progress state for a running import."""

    links_found: int = 0
    downloaded: int = 0
    cached: int = 0
    manual: int = 0
    linked: int = 0
    processed: int = 0
    errors: int = 0
    failed: bool = False
    recent_events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=100)
    )

    def update(self, event: dict[str, Any]) -> None:
        self.recent_events.append(event)
        event_name = event["event"]
        if event_name == "link_found":
            self.links_found += 1
        elif event_name == "document_downloaded":
            self.downloaded += 1
        elif event_name == "duplicate" and event.get("status") == "cached":
            self.cached += 1
        elif event_name == "manual_link":
            self.manual += 1
        elif event_name == "link_created":
            self.linked += 1
        elif event_name == "document_processed":
            self.processed += 1
        elif event_name == "error":
            self.errors += 1
        elif event_name == "failed":
            self.failed = True


def render_progress(state: ProgressState, placeholder: Any) -> None:
    with placeholder.container():
        st.write(
            f"Обработано документов: **{state.processed}** · "
            f"найдено ссылок: **{state.links_found}** · "
            f"создано связей: **{state.linked}**"
        )
        cols = st.columns(4)
        cols[0].metric("Скачано", state.downloaded)
        cols[1].metric("Дубликаты/кэш", state.cached)
        cols[2].metric("Manual", state.manual)
        cols[3].metric("Ошибки", state.errors)
        if state.recent_events:
            last = state.recent_events[-1]
            st.caption(
                f"Последнее событие: `{last['event']}` "
                f"{last.get('title') or last.get('url') or ''}"
            )


def run_import(
    *,
    root_url: str,
    local_path: Path | None,
    roles: tuple[str, ...],
    refresh: bool,
    deduplicate: bool,
    compact_versions: bool,
    placeholder: Any,
    predefined_document_ids: tuple[str, ...] = (),
    predefined_direction: str = "outgoing",
) -> ProgressState:
    progress = ProgressState()
    logger.info("import started root_url=%s local_path=%s", root_url, local_path)

    def on_event(event: dict[str, Any]) -> None:
        progress.update(event)
        logger.info("import event=%s payload=%s", event.get("event"), event)
        render_progress(progress, placeholder)

    with mutation_lock(), mutation_snapshot():
        store = CsvStore(DATA_DIR)
        before_document_ids = set(store.documents)
        try:
            GraphBuilder(store, PublicFetcher(), DocumentParser()).run(
                root_url=root_url or None,
                local_path=local_path,
                roles=roles,
                refresh=refresh,
                deduplicate=deduplicate,
                compact_versions=compact_versions,
                recreate_database=False,
                update_database=False,
                on_event=on_event,
                predefined_document_ids=predefined_document_ids,
                predefined_direction=predefined_direction,
            )
            graph_loader = GraphLoader(DATA_DIR, DATABASE_PATH)
            graph_loader.sync()
            graph_loader.delete_documents(before_document_ids - set(store.documents))
        except Exception as exc:
            logger.exception("import failed")
            if not any(event["event"] == "error" for event in progress.recent_events):
                on_event(
                    {
                        "event": "error",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
            on_event({"event": "failed", "reason": str(exc)})
            raise
    return progress


def delete_document(document_id: str) -> None:
    logger.info("delete started document_id=%s", document_id)
    with mutation_lock(), mutation_snapshot():
        store = CsvStore(DATA_DIR)
        if document_id not in store.documents:
            raise ValueError("Документ уже удалён")
        store.remove_document(document_id)
        store.clear_raw_paths()
        store.write()
        graph_loader = GraphLoader(DATA_DIR, DATABASE_PATH)
        graph_loader.delete_documents({document_id})
        graph_loader.sync()
    logger.info("delete completed document_id=%s", document_id)


def replace_error_document(document_id: str, uploaded_file: Any) -> None:
    """Replace an error placeholder with a manually uploaded document."""
    source_path = upload_path(uploaded_file)
    suffix = source_path.suffix.lower()
    source_format = "pdf" if suffix == ".pdf" else "html" if suffix in {".html", ".htm"} else "markdown"
    with mutation_lock(), mutation_snapshot():
        store = CsvStore(DATA_DIR)
        document = store.documents.get(document_id)
        if document is None:
            raise ValueError("Документ не найден")
        parser = DocumentParser()
        raw = source_path.read_bytes()
        parsed = parser.parse(raw, source_format, document.source_url, document.title)
        markdown = parser.to_markdown(raw, source_format, parsed.title, parsed)
        builder = GraphBuilder(store, PublicFetcher(), parser)
        document.title = parsed.title or document.title
        document.sha256 = hashlib.sha256(markdown).hexdigest()
        document.local_path = builder._save_markdown(markdown, document.id)
        document.raw_local_path = builder._save_raw(raw, document.id, suffix)
        document.source_format = source_format
        document.source_name = uploaded_file.name
        document.content_format = "markdown"
        document.is_active = True
        document.status = "downloaded"
        store.put_document(document)
        builder._register_version(document, document.sha256)
        builder.run(from_document_id=document.id, refresh=True, update_database=False)
        store.clear_raw_paths()
        store.write()
        GraphLoader(DATA_DIR, DATABASE_PATH).sync()


def delete_documents(document_ids: list[str]) -> int:
    """Delete several documents in one transaction."""
    logger.info("delete started document_ids=%s", document_ids)
    with mutation_lock(), mutation_snapshot():
        store = CsvStore(DATA_DIR)
        known = [d for d in document_ids if d in store.documents]
        if not known:
            raise ValueError("Документы уже удалены")
        before_document_ids = set(store.documents)
        for document_id in known:
            store.remove_document(document_id)
        store.clear_raw_paths()
        store.write()
        graph_loader = GraphLoader(DATA_DIR, DATABASE_PATH)
        graph_loader.delete_documents(before_document_ids - set(store.documents))
        graph_loader.sync()
    logger.info("delete completed count=%s", len(known))
    return len(known)


def document_rows() -> list[dict[str, str]]:
    rows = read_rows("Document.csv")
    role_rows = read_rows("FOR_ROLE.csv")
    roles = read_rows("Role.csv")
    role_names = {row["id"]: row["name"] for row in roles}
    by_document: dict[str, list[str]] = {}
    for row in role_rows:
        by_document.setdefault(row["from"], []).append(
            role_names.get(row["to"], row["to"])
        )
    for row in rows:
        row["roles"] = ", ".join(sorted(by_document.get(row["id"], [])))
        if row.get("source_type") == "local_file":
            row["source"] = row.get("source_name") or row.get("local_path", "")
        else:
            row["source"] = row.get("source_url", "")
    return rows


def render_import_tab() -> None:
    st.subheader("Ручная загрузка для ошибочного документа")
    error_rows = [row for row in document_rows() if row.get("status") == "error"]
    if error_rows:
        error_options = {f"{row['title']} · {row['id']}": row['id'] for row in error_rows}
        selected_error = st.selectbox("Документ со статусом error", list(error_options), key="error_document")
        replacement = st.file_uploader(
            "Файл для замены", type=["pdf", "md", "markdown", "html", "htm"], key="error_upload"
        )
        if st.button("Заменить ошибочный документ", type="primary"):
            if replacement is None:
                st.error("Выберите файл")
            else:
                try:
                    replace_error_document(error_options[selected_error], replacement)
                except Exception as exc:
                    logger.exception("manual replacement failed")
                    st.error(f"Ошибка ручной загрузки: {type(exc).__name__}: {exc}")
                else:
                    st.success("Документ заменён, CSV и LadybugDB обновлены")
                    st.rerun()
    else:
        st.info("Документов с ошибкой нет")

    st.divider()
    st.subheader("Добавить документ")
    mode = st.radio("Источник", ("Ссылка", "Файл"), horizontal=True)
    root_url = ""
    local_path: Path | None = None
    if mode == "Ссылка":
        root_url = st.text_input("URL документа", placeholder="https://...")
    else:
        uploaded = st.file_uploader(
            "PDF, Markdown или HTML", type=["pdf", "md", "markdown", "html", "htm"]
        )
        if uploaded:
            local_path = upload_path(uploaded)
            st.caption(f"Файл сохранён в кэше: `{uploaded.name}`")
    roles = tuple(st.multiselect("Роли", ["student", "support"]))
    existing_documents = document_rows()
    document_options = {
        f"{row['title']} · {row['id']}": row["id"]
        for row in existing_documents
    }
    predefined_labels = st.multiselect(
        "Связать с документами",
        list(document_options),
        help="Связи создаются только после успешной загрузки нового документа.",
    )
    direction_label = st.radio(
        "Направление связи",
        ("Новый документ → выбранные", "Выбранные → новый документ"),
        horizontal=True,
    )
    predefined_document_ids = tuple(document_options[label] for label in predefined_labels)
    predefined_direction = "outgoing" if direction_label.startswith("Новый") else "incoming"
    options = st.columns(3)
    refresh = options[0].checkbox("Обновить кэш")
    deduplicate = options[1].checkbox("Объединить дубликаты", value=True)
    compact = options[2].checkbox("Оставить текущие версии", value=False)
    if st.button("Запустить загрузку", type="primary"):
        if mode == "Ссылка" and not root_url.strip():
            st.error("Укажите URL")
            return
        if mode == "Файл" and local_path is None:
            st.error("Выберите файл")
            return
        events_placeholder = st.empty()
        with st.spinner("Обрабатываю документы и пересобираю LadybugDB..."):
            try:
                progress = run_import(
                    root_url=root_url.strip(),
                    local_path=local_path,
                    roles=roles,
                    refresh=refresh,
                    deduplicate=deduplicate,
                    compact_versions=compact,
                    placeholder=events_placeholder,
                    predefined_document_ids=predefined_document_ids,
                    predefined_direction=predefined_direction,
                )
            except Exception as exc:
                st.error(f"Ошибка загрузки: {type(exc).__name__}: {exc}")
            else:
                render_progress(progress, events_placeholder)
                st.success("Загрузка завершена, CSV и LadybugDB обновлены")
                st.rerun()


def render_documents_tab() -> None:
    st.subheader("Документы")
    rows = document_rows()
    if not rows:
        st.info("Документов пока нет")
        return
    selection = st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="documents_table",
    )
    selected_rows = selection.selection.rows
    if not selected_rows:
        st.info("Выберите одну или несколько строк для просмотра или удаления")
        return
    selected_documents = [rows[i] for i in selected_rows]
    selected = selected_documents[0]
    if len(selected_documents) == 1:
        st.caption(f"Выбран документ: {selected['title']} · {selected['id']}")
        links = read_rows("LINKS_TO.csv")
        incoming = sum(row["to"] == selected["id"] for row in links)
        outgoing = sum(row["from"] == selected["id"] for row in links)
        st.caption(f"Входящих связей: {incoming}. Исходящих связей: {outgoing}.")
        st.markdown("### Просмотр документа")
        local_path = _safe_project_path(selected.get("local_path", ""))
        if selected.get("status") == "error":
            st.error("Документ не удалось скачать")
            failures = [row for row in read_rows("manual_links.csv") if row.get("target_document_id") == selected["id"]]
            if failures:
                st.caption("Причина: " + failures[-1].get("reason", "неизвестная ошибка"))
        elif local_path and local_path.is_file():
            content = local_path.read_text(encoding="utf-8", errors="replace")
            st.download_button("Скачать Markdown", content, file_name=local_path.name, mime="text/markdown")
            st.markdown(content)
        else:
            st.info("Локальное содержимое документа отсутствует")
    else:
        st.caption(f"Выбрано документов: {len(selected_documents)}")
        for row in selected_documents:
            st.caption(f"- {row['title']} · {row['id']}")
    confirm = st.checkbox("Подтверждаю удаление выбранных документов и их связей")
    button_label = "Удалить документ" if len(selected_documents) == 1 else f"Удалить выбранные ({len(selected_documents)})"
    if st.button(button_label, type="secondary", disabled=not confirm):
        with st.status("Удаляем документы и их связи…", expanded=True) as status:
            try:
                removed = delete_documents([row["id"] for row in selected_documents])
            except Exception as exc:
                logger.exception("delete failed ids=%s", [row["id"] for row in selected_documents])
                status.update(label="Удаление не выполнено", state="error")
                st.error(f"Ошибка удаления: {type(exc).__name__}: {exc}")
            else:
                status.update(label="Документы удалены", state="complete")
                st.success(f"Удалено документов: {removed}. Связанные рёбра удалены.")
                st.rerun()


def render_links_tab() -> None:
    st.subheader("Связи документов")
    documents = {row["id"]: row.get("title", row["id"]) for row in read_rows("Document.csv")}
    links = read_rows("LINKS_TO.csv")
    for row in links:
        row["from_title"] = documents.get(row["from"], row["from"])
        row["to_title"] = documents.get(row["to"], row["to"])
    st.dataframe(links, use_container_width=True, hide_index=True)
    st.write("Ручная обработка")
    st.dataframe(
        read_rows("manual_links.csv"), use_container_width=True, hide_index=True
    )


def main() -> None:
    st.set_page_config(page_title="Vue Graph RAG MVP", layout="wide")
    st.title("Vue Graph RAG MVP")
    st.caption("Локальная загрузка документов, ссылок и изображений в CSV + LadybugDB")
    tab_import, tab_documents, tab_links = st.tabs(
        ["Загрузка", "Документы", "Ссылки и статусы"]
    )
    with tab_import:
        render_import_tab()
    with tab_documents:
        render_documents_tab()
    with tab_links:
        render_links_tab()


if __name__ == "__main__":
    main()
