from __future__ import annotations


def test_phase_one_public_module_locations_import() -> None:
    from vue_rag.application.dto import deletion, retrieval
    from vue_rag.application.dump_lifecycle import manifests
    from vue_rag.domain import base, graph, snapshot

    assert graph.Embedding.__module__ == "vue_rag.domain.graph"
    assert snapshot.GraphSnapshot.__module__ == "vue_rag.domain.snapshot"
    assert retrieval.VectorQuery.__module__ == "vue_rag.application.dto.retrieval"
    assert deletion.DeletionImpact.__module__ == "vue_rag.application.dto.deletion"
    assert (
        manifests.DumpManifest.__module__
        == "vue_rag.application.dump_lifecycle.manifests"
    )
    assert base.stable_id("node")
