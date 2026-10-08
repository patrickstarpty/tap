"""Integration test for `scripts/export-graph-samples.py` against a real,
isolated MySQL: publish one project-graph version with 5 EXTRACTED edges and 2
cross-source nodes, export both sample kinds, and assert the CSV row counts
and exact columns. Requires `TAP_RUN_MYSQL_INTEGRATION=1` (same convention as
`owned_project_mysql`); skips otherwise."""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql_project import publish_project_version
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    EdgeEvidence,
    MergeLogEntry,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)

_ANCHOR = {"kind": "text", "start": 0, "end": 5}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _load_export_module() -> ModuleType:
    path = _REPOSITORY_ROOT / "scripts" / "export-graph-samples.py"
    spec = importlib.util.spec_from_file_location("export_graph_samples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(_REPOSITORY_ROOT))
    spec.loader.exec_module(module)
    return module


def _draft() -> ProjectGraphDraft:
    nodes = tuple(
        ProjectNode(
            node_id=f"node-{i}",
            label=f"实体{i}",
            node_type="ENTITY",
            canonical_key=f"entity-{i}",
            degree=1,
        )
        for i in range(1, 7)
    )
    edges = tuple(
        ProjectEdge(
            edge_id=f"edge-{i}",
            source_node_id=f"node-{i}",
            target_node_id=f"node-{i + 1}",
            relation_type="GOVERNS",
            relation_label="需要",
            origin=RelationOrigin.EXTRACTED,
            confidence=0.9,
        )
        for i in range(1, 6)
    )
    edge_evidence = tuple(
        EdgeEvidence(
            edge_id=f"edge-{i}",
            source_revision_id=f"revision-{i}",
            document_revision_id=f"revision-{i}",
            chunk_id=f"chunk-{i}",
            anchor=_ANCHOR,
            content_digest="sha256:" + f"{i:064d}",
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_edge_id=f"fragment-edge-{i}",
        )
        for i in range(1, 6)
    )
    # Two cross-source merge candidates: node-1 and node-2 each have sources
    # spanning two distinct source revisions; node-3..6 have only one each.
    node_sources = (
        NodeSource(
            node_id="node-1",
            source_revision_id="revision-1",
            document_revision_id="revision-1",
            chunk_id="chunk-1",
            anchor=_ANCHOR,
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_node_id="fragment-node-1",
        ),
        NodeSource(
            node_id="node-1",
            source_revision_id="revision-2",
            document_revision_id="revision-2",
            chunk_id="chunk-2",
            anchor=_ANCHOR,
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_node_id="fragment-node-1b",
        ),
        NodeSource(
            node_id="node-2",
            source_revision_id="revision-1",
            document_revision_id="revision-1",
            chunk_id="chunk-1",
            anchor=_ANCHOR,
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_node_id="fragment-node-2",
        ),
        NodeSource(
            node_id="node-2",
            source_revision_id="revision-3",
            document_revision_id="revision-3",
            chunk_id="chunk-3",
            anchor=_ANCHOR,
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_node_id="fragment-node-2b",
        ),
        NodeSource(
            node_id="node-3",
            source_revision_id="revision-1",
            document_revision_id="revision-1",
            chunk_id="chunk-1",
            anchor=_ANCHOR,
            fragment_snapshot_id="fragment-snapshot-1",
            fragment_node_id="fragment-node-3",
        ),
    )
    merge_log = (
        MergeLogEntry(
            node_id="node-1",
            merged_from=(("fragment-snapshot-1", "fragment-node-1b"),),
            rule="EXACT",
        ),
        MergeLogEntry(
            node_id="node-2",
            merged_from=(("fragment-snapshot-1", "fragment-node-2b"),),
            rule="ALIAS",
        ),
    )
    return ProjectGraphDraft(
        fragment_digest="sha256:" + "a" * 64,
        nodes=nodes,
        edges=edges,
        node_sources=node_sources,
        edge_evidence=edge_evidence,
        aliases=(),
        communities=(),
        merge_log=merge_log,
    )


@pytest.mark.asyncio
async def test_export_edges_and_merges_writes_sampled_csv_rows_and_columns(
    owned_project_mysql, tmp_path
) -> None:
    module = _load_export_module()
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            await publish_project_version(
                session, VALIDATION_SCOPE, _draft(), version=1, now=datetime(2026, 10, 7, 9, 0, 0)
            )

        edges_output = tmp_path / "edge-sample.csv"
        merges_output = tmp_path / "merge-sample.csv"

        edges_result = await module.export_edges(
            sessions,
            blob_store=None,
            count=50,
            seed=1,
            output=edges_output,
            graph_version=1,
        )
        merges_result = await module.export_merges(
            sessions,
            count=30,
            seed=1,
            output=merges_output,
            graph_version=1,
        )
        assert edges_result == 0
        assert merges_result == 0

        with edges_output.open(encoding="utf-8", newline="") as handle:
            edge_rows = list(csv.DictReader(handle))
        with merges_output.open(encoding="utf-8", newline="") as handle:
            merge_rows = list(csv.DictReader(handle))

        assert len(edge_rows) == 5
        assert all(set(row) == set(module.EDGE_COLUMNS) for row in edge_rows)
        assert all(row["origin"] == "EXTRACTED" for row in edge_rows)
        assert all(row["verdict"] == "" for row in edge_rows)

        assert len(merge_rows) == 2
        assert all(set(row) == set(module.MERGE_COLUMNS) for row in merge_rows)
        assert {row["nodeId"] for row in merge_rows} == {"node-1", "node-2"}
        assert all(row["sourceCount"] == "2" for row in merge_rows)
    finally:
        await engine.dispose()
