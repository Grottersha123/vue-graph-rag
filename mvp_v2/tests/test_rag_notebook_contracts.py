from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, cast

NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "rag_batch_debug.ipynb"


def _cells() -> list[dict[str, Any]]:
    payload = cast(dict[str, object], json.loads(NOTEBOOK.read_text(encoding="utf-8")))
    return cast(list[dict[str, Any]], payload["cells"])


def test_rag_notebook_code_cells_compile_with_top_level_await() -> None:
    for index, cell in enumerate(_cells()):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cast(list[str], cell["source"]))
        compile(
            source,
            f"rag_batch_debug.ipynb:cell-{index}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )


def test_rag_notebook_reloads_contract_types_before_service_aliases() -> None:
    source = next(
        "".join(cast(list[str], cell["source"]))
        for cell in _cells()
        if "SearchRequest = retrieval_dto_module.SearchRequest"
        in "".join(cast(list[str], cell["source"]))
    )
    ordered_markers = (
        "ingestion_contracts_module = importlib.reload",
        "retrieval_dto_module = importlib.reload",
        "chat_dto_module = importlib.reload",
        "manifests_module = importlib.reload",
        "ladybug_adapter_module = importlib.reload",
        "retrieval_module = importlib.reload",
        "chat_module = importlib.reload",
        "SearchRequest = retrieval_dto_module.SearchRequest",
        "SearchService = retrieval_module.SearchService",
        "LadybugAnn = ladybug_adapter_module.LadybugAnn",
        "database_manifest = DatabaseManifest.model_validate_json",
        'if "minimum_score" not in SearchRequest.model_fields:',
    )

    positions = tuple(source.index(marker) for marker in ordered_markers)
    assert positions == tuple(sorted(positions))
