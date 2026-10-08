"""`RelationAnalysisAgent`: the deterministic relation analysis pipeline
(`tap.modules.knowledge.application.relation_analysis`) run as a contract
agent subgraph.

The agent pins one project graph version for its whole run (`get_current`
at the start, rechecked once more after ranking), walks
seed -> expand -> path -> rank -> assemble, authorizes every resolved
support against the current publication, and reports its outcome entirely
through `RelationContext.status` -- per `AgentSubgraph`'s contract, `run`
never raises.
"""

from __future__ import annotations

from typing import Literal, Mapping

from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.graph.domain.project import EdgeEvidence, ProjectEdge, ProjectNode
from tap.modules.graph.ports.project_store import ProjectGraphStorePort
from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority
from tap.modules.knowledge.application.relation_analysis import (
    RelationAnalysisInput,
    RelationContext,
    RelationContextStatus,
    RelationSupport,
    SeedNode,
    assemble,
    expand,
    expansion_depth,
    find_paths,
    rank_edges,
    seed_from_evidence,
    seed_from_query,
    snippet_chunk_refs,
)
from tap.modules.knowledge.ports.snippets import ChunkSnippetReader
from tap.platform.telemetry import span

__all__ = ["RelationAnalysisAgent"]

_MAX_AUGMENT_CHUNKS = 5


_Origin = Literal["evidence", "query"]


def _combine_seeds(
    evidence_seeds: tuple[ProjectNode, ...], query_seeds: tuple[ProjectNode, ...]
) -> tuple[tuple[ProjectNode, ...], dict[str, _Origin]]:
    combined: list[ProjectNode] = []
    origins: dict[str, _Origin] = {}
    for node in evidence_seeds:
        if node.node_id not in origins:
            origins[node.node_id] = "evidence"
            combined.append(node)
    for node in query_seeds:
        if node.node_id not in origins:
            origins[node.node_id] = "query"
            combined.append(node)
    return tuple(combined), origins


def _visible_seed_nodes(
    combined: tuple[ProjectNode, ...],
    origins: Mapping[str, _Origin],
    nodes_by_id: Mapping[str, ProjectNode],
) -> tuple[SeedNode, ...]:
    """Only seeds that survived the filtered `expand` (i.e. are still present
    in the resulting subgraph) may be exposed -- a seed dropped by the
    source-revision filter must not leak its label past the selection."""
    return tuple(
        SeedNode(
            node_id=node.node_id,
            label=nodes_by_id[node.node_id].label,
            origin=origins[node.node_id],
        )
        for node in combined
        if node.node_id in nodes_by_id
    )


def _approved_item_id(anchor: Mapping[str, object]) -> str | None:
    value = anchor.get("inventoryItemId")
    return value if isinstance(value, str) else None


class RelationAnalysisAgent:
    """Runs the relation analysis pipeline as a single agent subgraph."""

    name = "relation-analysis"
    input_schema = RelationAnalysisInput
    output_schema = RelationContext

    def __init__(
        self,
        store: ProjectGraphStorePort,
        snippets: ChunkSnippetReader,
        *,
        publication_authority: PublishedKnowledgeAuthority | None = None,
    ) -> None:
        self._store = store
        self._snippets = snippets
        self._publication_authority = publication_authority

    async def run(self, context: AgentContext, value: RelationAnalysisInput) -> RelationContext:
        try:
            current = await self._store.get_current(context.scope)
        except Exception:
            return RelationContext(
                status=RelationContextStatus.FAILED,
                graph_version=None,
                diagnostics={"failure": 1},
            )

        if current is None:
            return RelationContext(status=RelationContextStatus.NOT_READY, graph_version=None)
        if current.status != "READY":
            return RelationContext(
                status=RelationContextStatus.NOT_READY, graph_version=str(current.version)
            )
        if value.graph_version and value.graph_version != str(current.version):
            return RelationContext(
                status=RelationContextStatus.STALE, graph_version=value.graph_version
            )

        version = current.version
        scope = context.scope
        allowed = value.source_revision_ids

        try:
            with span("graph.seed", context=context.parent_context) as seed_span:
                evidence_seeds = await seed_from_evidence(
                    self._store, scope, version, value.evidence
                )
                query_seeds = await seed_from_query(
                    self._store,
                    scope,
                    version,
                    value.query,
                    allowed_source_revision_ids=allowed,
                )
                seed_span.set_attribute("tap.graph.evidence_seeds", len(evidence_seeds))
                seed_span.set_attribute("tap.graph.query_seeds", len(query_seeds))

            all_seed_nodes, origins = _combine_seeds(evidence_seeds, query_seeds)
            seed_ids = frozenset(node.node_id for node in all_seed_nodes)

            with span("graph.expand", context=context.parent_context) as expand_span:
                subgraph = await expand(
                    self._store,
                    scope,
                    version,
                    all_seed_nodes,
                    allowed_source_revision_ids=allowed,
                )
                expand_span.set_attribute("tap.graph.node_count", len(subgraph.nodes))
                expand_span.set_attribute("tap.graph.edge_count", len(subgraph.edges))
                expand_span.set_attribute("tap.graph.depth", expansion_depth(len(all_seed_nodes)))

            with span("graph.path", context=context.parent_context) as path_span:
                paths = await find_paths(
                    self._store,
                    scope,
                    version,
                    query_seeds,
                    evidence_seeds,
                    allowed_source_revision_ids=allowed,
                )
                path_span.set_attribute("tap.graph.path_count", len(paths))

            with span("relation.rank", context=context.parent_context) as rank_span:
                ranked_pairs = rank_edges(subgraph, paths, seed_ids)
                evidence_by_edge: dict[str, list[EdgeEvidence]] = {}
                for item in subgraph.evidence:
                    evidence_by_edge.setdefault(item.edge_id, []).append(item)
                ranked: tuple[tuple[ProjectEdge, bool, tuple[EdgeEvidence, ...]], ...] = tuple(
                    (edge, on_path, tuple(evidence_by_edge.get(edge.edge_id, ())))
                    for edge, on_path in ranked_pairs
                )
                nodes_by_id = {node.node_id: node for node in subgraph.nodes}
                refs = snippet_chunk_refs(
                    ranked, value.evidence, allowed_source_revision_ids=allowed
                )
                snippet_map = await self._snippets.snippets(refs)
                document_id_map = await self._snippets.document_ids(
                    tuple(dict.fromkeys(document_revision_id for document_revision_id, _ in refs))
                )
                relations = assemble(
                    ranked,
                    nodes_by_id,
                    value.evidence,
                    snippet_map,
                    allowed_source_revision_ids=allowed,
                    document_ids=document_id_map,
                )
                rank_span.set_attribute("tap.relation.count", len(relations))

            if self._publication_authority is not None:
                for relation in relations:
                    for support in relation.support:
                        await self._publication_authority.authorize_evidence(
                            scope.project_id,
                            source_revision_id=support.source_revision_id,
                            document_revision_id=support.document_revision_id,
                            approved_item_id=_approved_item_id(support.anchor),
                        )

            refreshed = await self._store.get_current(scope)
            if refreshed is None or refreshed.version != version:
                return RelationContext(
                    status=RelationContextStatus.STALE,
                    graph_version=value.graph_version or str(version),
                )

            seeds_out = _visible_seed_nodes(all_seed_nodes, origins, nodes_by_id)

            if not all_seed_nodes or not relations:
                return RelationContext(
                    status=RelationContextStatus.EMPTY,
                    graph_version=str(version),
                    seeds=seeds_out,
                    paths=paths,
                )

            # Augment candidates are not run through `_publication_authority`
            # here -- only the relations' own support is (above). They are
            # unauthorized snippet candidates for later retrieval fusion;
            # Task 5's `authorize_selection` re-check at fusion time is what
            # actually gates them before they can reach a prompt or event.
            augment_chunks = self._augment_chunks(
                refs, snippet_map, _evidence_by_ref(subgraph.evidence), document_id_map
            )

            return RelationContext(
                status=RelationContextStatus.APPLIED,
                graph_version=str(version),
                seeds=seeds_out,
                relations=relations,
                paths=paths,
                augment_chunks=augment_chunks,
            )
        except Exception:
            return RelationContext(
                status=RelationContextStatus.FAILED,
                graph_version=str(version),
                diagnostics={"failure": 1},
            )

    @staticmethod
    def _augment_chunks(
        refs: tuple[tuple[str, str], ...],
        snippet_map: Mapping[str, str],
        evidence_by_ref: Mapping[tuple[str, str], EdgeEvidence],
        document_id_map: Mapping[str, str],
    ) -> tuple[RelationSupport, ...]:
        augmented: list[RelationSupport] = []
        for ref in refs:
            if len(augmented) >= _MAX_AUGMENT_CHUNKS:
                break
            document_revision_id, chunk_id = ref
            snippet = snippet_map.get(chunk_id)
            evidence_item = evidence_by_ref.get(ref)
            if snippet is None or evidence_item is None:
                continue
            augmented.append(
                RelationSupport(
                    chunk_id=evidence_item.chunk_id,
                    source_revision_id=evidence_item.source_revision_id,
                    document_revision_id=evidence_item.document_revision_id,
                    content_digest=evidence_item.content_digest,
                    anchor=evidence_item.anchor,
                    evidence_label=None,
                    snippet=snippet[:300],
                    document_id=document_id_map.get(document_revision_id),
                )
            )
        return tuple(augmented)


def _evidence_by_ref(evidence: tuple[EdgeEvidence, ...]) -> dict[tuple[str, str], EdgeEvidence]:
    by_ref: dict[tuple[str, str], EdgeEvidence] = {}
    for item in evidence:
        by_ref.setdefault((item.document_revision_id, item.chunk_id), item)
    return by_ref
