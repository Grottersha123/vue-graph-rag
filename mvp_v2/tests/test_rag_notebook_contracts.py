from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, cast

NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "rag_batch_debug.ipynb"


def _cells() -> list[dict[str, Any]]:
    payload = cast(dict[str, object], json.loads(NOTEBOOK.read_text(encoding="utf-8")))
    return cast(list[dict[str, Any]], payload["cells"])


def _literal_dict_keys(node: ast.AST) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def test_batch_artifacts_keep_run_metadata_out_of_case_results() -> None:
    result_keys: list[set[str]] = []
    run_keys: list[set[str]] = []
    traced_prompt_keys: set[str] = set()
    stale_prompt_locations: list[str] = []

    for cell in _cells():
        if cell["cell_type"] != "code":
            continue
        source = "".join(cast(list[str], cell["source"]))
        stale_prompt_locations.append(source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            target_names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if "result" in target_names:
                result_keys.append(_literal_dict_keys(node.value))
            if "RUN_METADATA" in target_names:
                run_keys.append(_literal_dict_keys(node.value))
        if "class TracedModel:" in source:
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    traced_prompt_keys.update(_literal_dict_keys(node))

    assert len(result_keys) == 2
    assert result_keys[0] == result_keys[1]
    assert not (
        result_keys[0]
        & {"database", "chat_model", "prompt_profile", "minimum_similarity_score"}
    )
    assert len(run_keys) == 2
    assert all(
        {"database", "chat_model", "minimum_similarity_score", "prompt"}
        <= keys
        for keys in run_keys
    )
    assert "prompt" not in traced_prompt_keys
    assert all(
        "results.json[*].generation_calls[*].prompt" not in source
        for source in stale_prompt_locations
    )


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
