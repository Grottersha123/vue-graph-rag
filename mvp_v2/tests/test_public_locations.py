from __future__ import annotations


def test_phase_one_public_module_locations_import() -> None:
    from mvp_v2.application.dto import deletion, retrieval
    from mvp_v2.application.dump_lifecycle import manifests
    from mvp_v2.domain import base, graph, schema_registry, snapshot

    assert graph.Embedding.__module__ == "mvp_v2.domain.graph"
    assert snapshot.GraphSnapshot.__module__ == "mvp_v2.domain.snapshot"
    assert retrieval.VectorQuery.__module__ == "mvp_v2.application.dto.retrieval"
    assert deletion.DeletionImpact.__module__ == "mvp_v2.application.dto.deletion"
    assert (
        manifests.DumpManifest.__module__
        == "mvp_v2.application.dump_lifecycle.manifests"
    )
    assert base.stable_id("node")
    assert schema_registry.registry_errors() == ()
