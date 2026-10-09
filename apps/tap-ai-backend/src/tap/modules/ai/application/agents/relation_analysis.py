"""`RelationAnalysisAgent`: the deterministic relation analysis pipeline
(`tap.modules.knowledge.application.relation_analysis`) run as a contract
agent subgraph.

The agent pins one project graph version for its whole run (`get_current`
at the start, rechecked once more after ranking), walks
seed -> expand -> path -> rank -> assemble, authorizes every resolved
support against the current publication, and reports its outcome entirely
through `RelationContext.status` -- per `AgentSubgraph`'s contract, `run`
never raises.

Caps against unbounded per-answer graph work: see `_MAX_EVIDENCE_SEED_REFS`
and `_MAX_PATH_ATTEMPTS` below, and `_resolve_support`'s per-relation support
cap in the pipeline module. There is deliberately no additional "resolve the
graph once per run" step here: this agent pins one explicit `version` (see
above) for every store call it makes after its initial `get_current`, and
`MysqlProjectGraphStore._loaded` (the `ProjectGraphStorePort` implementation
backing every `nodes`/`neighbors`/`path`/`match_aliases` call) returns the
cached `LoadedProjectGraph` for an already-cached, explicitly pinned version
without issuing a `get_current` MySQL `SELECT` first -- a READY version's
rows are immutable once merged, so a cache hit for a pinned version is never
stale, and this agent's own post-ranking `get_current` recheck (below) is
what catches a version bump that happened mid-run.
"""

from __future__ import annotations

import dataclasses
from typing import Literal, Mapping

from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.graph.domain.project import EdgeEvidence, ProjectEdge, ProjectNode
from tap.modules.graph.ports.project_store import (
    ProjectGraphStorePort,
    ProjectGraphVersionMismatch,
)
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublicationBinding,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.application.relation_analysis import (
    RelationAnalysisInput,
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
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
# Caps against unbounded per-answer graph work: only the first 10 S-labelled
# evidence refs (in the order the caller gave them, i.e. S1..S10) ever seed a
# node lookup; `find_paths` itself bounds its own `store.path` round-trips
# (see `_MAX_PATH_ATTEMPTS`); per-relation support is bounded inside
# `_resolve_support` (S-labelled supports are never capped, at most two fresh
# snippet-only supports are).
_MAX_EVIDENCE_SEED_REFS = 10
_MAX_PATH_ATTEMPTS = 20


_Origin = Literal["evidence", "query"]


def _combine_seeds(
    query_seeds: tuple[ProjectNode, ...], evidence_seeds: tuple[ProjectNode, ...]
) -> tuple[tuple[ProjectNode, ...], dict[str, _Origin]]:
    """Query seeds are combined ahead of evidence seeds so their node IDs
    occupy the earlier, protected slots in `expand()`'s `node_limit` cut
    (`expand` keeps seeds in the order it is given them, cutting the overflow
    from the back): a question that names an entity by itself must not lose
    that entity's edges to a flood of evidence-chunk seeds ranked first."""
    combined: list[ProjectNode] = []
    origins: dict[str, _Origin] = {}
    for node in query_seeds:
        if node.node_id not in origins:
            origins[node.node_id] = "query"
            combined.append(node)
    for node in evidence_seeds:
        if node.node_id not in origins:
            origins[node.node_id] = "evidence"
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


def _flowchart_support_allowed(
    anchor: Mapping[str, object],
    source_revision_id: str,
    binding: PublicationBinding | None,
) -> bool:
    """Mirrors `retrieve.py`'s `_flowchart_hit_is_approved`: an anchor with a
    `bbox` is an image-region hit whose meaning a model proposed, so it may
    only support an answer as an approved item of the current publication --
    everything else (plain text chunks) answers unconditionally, same as
    today."""
    if not anchor.get("bbox"):
        return True
    if binding is None or source_revision_id not in binding.source_revision_ids:
        return False
    item_id = _approved_item_id(anchor)
    if item_id is None:
        return False
    return item_id in binding.for_revision(source_revision_id).approved_item_ids


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
        flowchart_gate: FlowchartPublicationGate | None = None,
    ) -> None:
        self._store = store
        self._snippets = snippets
        self._publication_authority = publication_authority
        self._flowchart_gate = flowchart_gate

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
                    self._store, scope, version, value.evidence[:_MAX_EVIDENCE_SEED_REFS]
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

            all_seed_nodes, origins = _combine_seeds(query_seeds, evidence_seeds)
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
                path_expansion = await find_paths(
                    self._store,
                    scope,
                    version,
                    query_seeds,
                    evidence_seeds,
                    allowed_source_revision_ids=allowed,
                    max_attempts=_MAX_PATH_ATTEMPTS,
                )
                paths = path_expansion.paths
                path_span.set_attribute("tap.graph.path_count", len(paths))

            with span("relation.rank", context=context.parent_context) as rank_span:
                flowchart_binding: PublicationBinding | None = None
                if self._flowchart_gate is not None:
                    flowchart_binding = await self._flowchart_gate.current(scope.project_id)

                ranked_pairs = rank_edges(
                    subgraph, paths, seed_ids, extra_edges=path_expansion.edges
                )
                evidence_by_edge: dict[str, list[EdgeEvidence]] = {}
                for item in (*subgraph.evidence, *path_expansion.evidence):
                    evidence_by_edge.setdefault(item.edge_id, []).append(item)
                ranked: tuple[tuple[ProjectEdge, bool, tuple[EdgeEvidence, ...]], ...] = tuple(
                    (
                        edge,
                        on_path,
                        tuple(
                            item
                            for item in evidence_by_edge.get(edge.edge_id, ())
                            if _flowchart_support_allowed(
                                item.anchor, item.source_revision_id, flowchart_binding
                            )
                        ),
                    )
                    for edge, on_path in ranked_pairs
                )
                # `path_expansion.nodes` covers path endpoints/intermediates
                # that `expand()`'s node_limit cut left outside `subgraph`
                # (same reason `extra_edges` above exists): without them, a
                # path edge merged into `ranked` would still get dropped by
                # `assemble()`, which looks up each edge's endpoints in
                # `nodes_by_id` and skips the edge if either is missing.
                nodes_by_id = {node.node_id: node for node in subgraph.nodes}
                for node in path_expansion.nodes:
                    nodes_by_id.setdefault(node.node_id, node)
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
                relations = await self._authorize_relations(relations, scope.project_id)

            refreshed = await self._store.get_current(scope)
            if refreshed is None or refreshed.version != version:
                return RelationContext(
                    status=RelationContextStatus.STALE,
                    graph_version=value.graph_version or str(version),
                )

            seeds_out = _visible_seed_nodes(all_seed_nodes, origins, nodes_by_id)

            # A path is only worth surfacing (e.g. in a prompt's "related via"
            # hint) if every one of its edges actually became a citable R
            # relation -- otherwise the path would point at an edge the reader
            # can never see cited, which is more misleading than no path at all.
            assembled_edge_ids = {relation.edge_id for relation in relations}
            visible_paths = tuple(
                path
                for path in paths
                if path.edge_ids and all(edge_id in assembled_edge_ids for edge_id in path.edge_ids)
            )

            if not all_seed_nodes or not relations:
                return RelationContext(
                    status=RelationContextStatus.EMPTY,
                    graph_version=str(version),
                    seeds=seeds_out,
                    paths=visible_paths,
                )

            # Augment candidates are not run through `_publication_authority`
            # here -- only the relations' own support is (above). They are
            # unauthorized snippet candidates for later retrieval fusion;
            # `PublishedKnowledgeAuthority.authorize_selection`'s re-check at
            # fusion time is what actually gates them before they can reach a
            # prompt or event.
            augment_chunks = self._augment_chunks(
                refs, snippet_map, _evidence_by_ref(subgraph.evidence), document_id_map
            )

            return RelationContext(
                status=RelationContextStatus.APPLIED,
                graph_version=str(version),
                seeds=seeds_out,
                relations=relations,
                paths=visible_paths,
                augment_chunks=augment_chunks,
            )
        except ProjectGraphVersionMismatch:
            # The pinned `version` fell outside the store's retention window
            # mid-run (a background merge published far enough ahead that the
            # version this run started with was evicted) -- the answer is
            # stale, not broken; the caller may retry against the new current
            # version instead of surfacing a bare failure.
            return RelationContext(
                status=RelationContextStatus.STALE,
                graph_version=value.graph_version or str(version),
            )
        except Exception:
            return RelationContext(
                status=RelationContextStatus.FAILED,
                graph_version=str(version),
                diagnostics={"failure": 1},
            )

    async def _authorize_relations(
        self, relations: tuple[RelationEvidence, ...], project_id: str
    ) -> tuple[RelationEvidence, ...]:
        """Authorize each relation's support against the current publication
        one support at a time: a denied support is dropped from its relation,
        not the whole run -- a relation left with no surviving support is
        itself dropped, but every other relation (and the context as a whole)
        still applies."""
        assert self._publication_authority is not None
        kept: list[RelationEvidence] = []
        for relation in relations:
            kept_support: list[RelationSupport] = []
            for support in relation.support:
                try:
                    await self._publication_authority.authorize_evidence(
                        project_id,
                        source_revision_id=support.source_revision_id,
                        document_revision_id=support.document_revision_id,
                        approved_item_id=_approved_item_id(support.anchor),
                    )
                except AuthorizationDenied:
                    continue
                kept_support.append(support)
            if kept_support:
                kept.append(dataclasses.replace(relation, support=tuple(kept_support)))
        return tuple(kept)

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
