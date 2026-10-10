"""Integration test for `scripts/export-graph-samples.py` against a real,
isolated MySQL: publish project-graph versions with EXTRACTED edges and
cross-source nodes, export both sample kinds, and assert the CSV row counts,
exact columns, version selection, title/snippet resolution (via
`MysqlGraphNodeEnrichment` with a fake blob store), the blank-snippet/title
failure path, and the fewer-than-requested warning. Requires
`TAP_RUN_MYSQL_INTEGRATION=1` (same convention as `owned_project_mysql`);
skips otherwise."""

from __future__ import annotations

import csv
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.graph.adapters.mysql import graph_extraction_job, graph_snapshot
from tap.modules.graph.adapters.mysql_project import graph_project_version, publish_project_version
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import (
    EdgeEvidence,
    MergeLogEntry,
    NodeSource,
    ProjectEdge,
    ProjectGraphDraft,
    ProjectNode,
)
from tap.modules.knowledge.adapters.mysql_documents import (
    knowledge_document,
    knowledge_document_revision,
    knowledge_source,
)
from tap.modules.knowledge.domain.documents import ChunkDraft
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.modules.knowledge.ports.errors import ArtifactUnavailable
from tap.platform.db.project_scope import scope_values

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


@dataclass
class FakeArtifactStore:
    """Minimal `ArtifactStore` stand-in: maps a locator string straight to the
    chunks it holds, raising `ArtifactUnavailable` (caught by
    `MysqlGraphNodeEnrichment.snippet`) for anything unregistered."""

    chunks_by_locator: dict[str, tuple[ChunkDraft, ...]]

    async def read_chunks(self, locator: ArtifactLocator) -> tuple[ChunkDraft, ...]:
        try:
            return self.chunks_by_locator[str(locator)]
        except KeyError:
            raise ArtifactUnavailable(f"no fake chunks for locator {locator}") from None


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


async def _seed_knowledge_chain(
    session: AsyncSession, *, revision_ids: list[str], now: datetime
) -> dict[str, tuple[ChunkDraft, ...]]:
    """Seed one source/document plus one revision + extraction job per
    `revision_ids` entry, and return the fake blob store's locator->chunks
    map so `MysqlGraphNodeEnrichment.snippet` can resolve real content."""
    await session.execute(
        insert(knowledge_source).values(
            **scope_values(VALIDATION_SCOPE),
            source_id="source-1",
            name="AIA policy terms",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
    )
    await session.execute(
        insert(knowledge_document).values(
            **scope_values(VALIDATION_SCOPE),
            document_id="document-1",
            source_id="source-1",
            filename="policy.pdf",
            media_type="application/pdf",
            current_revision_id=None,
            source_content_hash="sha256:" + "0" * 64,
            dedupe_key=None,
            staging_blob_locator=None,
            promoted_blob_locator=None,
            reservation_owner_token=None,
            reservation_expires_at=None,
            reservation_parser_version="tapper-parser-v1",
            reservation_chunker_version="tapper-chunker-v1",
            reservation_pipeline_version="tapper-ingestion-v1",
            status="ready",
            stage="ready",
            chunk_count=1,
            error_code=None,
            error_summary=None,
            activated_at=now,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
    )
    await session.execute(
        insert(graph_snapshot).values(
            **scope_values(VALIDATION_SCOPE),
            snapshot_id="snapshot-1",
            source_set_digest="sha256:" + "1" * 64,
            source_revision_ids=revision_ids,
            document_revision_ids=revision_ids,
            status="READY",
            created_at=now,
        )
    )
    chunks_by_locator: dict[str, tuple[ChunkDraft, ...]] = {}
    for index, revision_id in enumerate(revision_ids):
        chunk_id = f"chunk-{revision_id.removeprefix('revision-')}"
        locator = f"fake-locator-{revision_id}"
        await session.execute(
            insert(knowledge_document_revision).values(
                **scope_values(VALIDATION_SCOPE),
                revision_id=revision_id,
                source_id="source-1",
                chunk_manifest_digest=None,
                projection_digest=None,
                document_id="document-1",
                source_content_hash=f"sha256:{index:064d}",
                original_blob_locator=f"tapper-originals/{revision_id}",
                normalized_blob_locator=None,
                chunks_blob_locator=None,
                embeddings_blob_locator=None,
                parser_version="tapper-parser-v1",
                chunker_version="tapper-chunker-v1",
                pipeline_version="tapper-ingestion-v1",
                parse_inventory_attempt=1,
                parser_config_digest=None,
                parse_inventory_digest=None,
                created_at=now,
            )
        )
        await session.execute(
            insert(graph_extraction_job).values(
                **scope_values(VALIDATION_SCOPE),
                job_id=f"job-{revision_id}",
                snapshot_id="snapshot-1",
                revision_id=revision_id,
                chunks_locator=locator,
                extraction_profile_digest="sha256:" + "2" * 64,
                model_alias="fake-model",
                request_digest=f"sha256:{index:064d}",
                status="SUCCEEDED",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                attempt_count=0,
                failure_code=None,
                created_at=now,
                updated_at=now,
            )
        )
        chunks_by_locator[locator] = (
            ChunkDraft(
                chunk_id=chunk_id,
                logical_chunk_id=chunk_id,
                root_id="document-1",
                parent_id=None,
                content=f"投保人应于合同成立之日起{index + 1}日内缴纳保费。",
                anchor_json="{}",
                source_content_hash=f"sha256:{index:064d}",
                chunk_content_hash=f"sha256:{index:064d}",
            ),
        )
    return chunks_by_locator


@pytest.mark.asyncio
async def test_export_edges_and_merges_writes_sampled_csv_rows_and_columns(
    owned_project_mysql, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no knowledge-module rows seeded, every edge's snippet/title stays
    blank: `export_edges` must still write the CSV but exit 1 with a count in
    its stderr warning (I1/I2's failure outcome). Requesting more rows than
    exist (50/30 vs 5/2) must also warn, without changing the exit code for
    merges (M3)."""
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
        enrichment = module.MysqlGraphNodeEnrichment(sessions, FakeArtifactStore({}))

        edges_result = await module.export_edges(
            sessions, enrichment, count=50, seed=1, output=edges_output, graph_version=1
        )
        merges_result = await module.export_merges(
            sessions, enrichment, count=30, seed=1, output=merges_output, graph_version=1
        )
        captured = capsys.readouterr()

        assert edges_result == 1
        assert merges_result == 0
        assert "warning: requested 50 edge samples but only 5 are available" in captured.err
        assert "warning: requested 30 merge samples but only 2 are available" in captured.err
        assert "warning: 5 edge sample(s) have a blank snippet or source title" in captured.err

        with edges_output.open(encoding="utf-8", newline="") as handle:
            edge_rows = list(csv.DictReader(handle))
        with merges_output.open(encoding="utf-8", newline="") as handle:
            merge_rows = list(csv.DictReader(handle))

        assert len(edge_rows) == 5
        assert all(set(row) == set(module.EDGE_COLUMNS) for row in edge_rows)
        assert all(row["origin"] == "EXTRACTED" for row in edge_rows)
        assert all(row["verdict"] == "" for row in edge_rows)
        assert all(row["sourceTitle"] == "" and row["evidenceSnippet"] == "" for row in edge_rows)

        assert len(merge_rows) == 2
        assert all(set(row) == set(module.MERGE_COLUMNS) for row in merge_rows)
        assert {row["nodeId"] for row in merge_rows} == {"node-1", "node-2"}
        assert all(row["sourceCount"] == "2" for row in merge_rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_export_picks_current_ready_version_and_resolves_titles_and_snippets(
    owned_project_mysql, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Version 1 is READY and version 2 is MERGING; omitting `--graph-version`
    must sample version 1 (M1). With the full knowledge chain seeded and a
    fake blob store wired through `MysqlGraphNodeEnrichment`, every edge's
    title/snippet resolves (I1/I2's success outcome): no blank warning, exit
    0."""
    module = _load_export_module()
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(session, VALIDATION_SCOPE, _draft(), version=1, now=now)
        async with sessions() as session, session.begin():
            await publish_project_version(session, VALIDATION_SCOPE, _draft(), version=2, now=now)
            await session.execute(
                update(graph_project_version)
                .where(
                    graph_project_version.c.project_id == VALIDATION_SCOPE.project_id,
                    graph_project_version.c.version == 2,
                )
                .values(status="MERGING")
            )
        async with sessions() as session, session.begin():
            chunks_by_locator = await _seed_knowledge_chain(
                session, revision_ids=[f"revision-{i}" for i in range(1, 6)], now=now
            )

        enrichment = module.MysqlGraphNodeEnrichment(sessions, FakeArtifactStore(chunks_by_locator))
        output = tmp_path / "edge-sample.csv"
        result = await module.export_edges(
            sessions, enrichment, count=50, seed=1, output=output, graph_version=None
        )
        captured = capsys.readouterr()

        assert result == 0
        assert "blank" not in captured.err

        with output.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 5
        assert all(row["graphVersion"] == "1" for row in rows)
        assert all(row["sourceTitle"] == "AIA policy terms" for row in rows)
        assert all(row["evidenceSnippet"] for row in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_export_rejects_a_graph_version_with_no_ready_row(
    owned_project_mysql, tmp_path
) -> None:
    """M2: a `--graph-version` that is not `READY` (or does not exist) must
    be rejected with a clear message instead of silently sampling it or an
    empty version."""
    module = _load_export_module()
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        now = datetime(2026, 10, 7, 9, 0, 0)
        async with sessions() as session, session.begin():
            await publish_project_version(session, VALIDATION_SCOPE, _draft(), version=1, now=now)
            await session.execute(
                update(graph_project_version)
                .where(
                    graph_project_version.c.project_id == VALIDATION_SCOPE.project_id,
                    graph_project_version.c.version == 1,
                )
                .values(status="MERGING")
            )

        enrichment = module.MysqlGraphNodeEnrichment(sessions, FakeArtifactStore({}))
        with pytest.raises(ValueError, match="READY"):
            await module.export_edges(
                sessions,
                enrichment,
                count=50,
                seed=1,
                output=tmp_path / "edges.csv",
                graph_version=1,
            )
        with pytest.raises(ValueError, match="READY"):
            await module.export_edges(
                sessions,
                enrichment,
                count=50,
                seed=1,
                output=tmp_path / "edges.csv",
                graph_version=99,
            )
    finally:
        await engine.dispose()
