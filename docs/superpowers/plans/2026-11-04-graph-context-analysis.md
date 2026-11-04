# Graph Context Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ground relation answers in authorized snapshot expansion and persist navigable relation citations alongside original-text citations.

**Architecture:** Keep `classify → admit → execute`. Expand the active immutable snapshot after authorized retrieval, map chunk IDs through `SnapshotIndex.chunk_facts`, and expose read-only graph tools within the existing four-step LangGraph loop. Resolve R labels only against this Turn's graph context and persist relation citations with its immutable answer evidence.

**Tech Stack:** Python 3.13, FastAPI/Pydantic, LangGraph, SQLAlchemy/MySQL, pytest, existing deterministic golden runner.

**Spec:** [Graph context analysis](../specs/2026-11-03-graph-context-analysis-design.md)

## Global Constraints

- Only selected sources' active snapshots; no multi-snapshot reasoning.
- Expansion uses 1 hop for facts and 2 hops for relation intent, at most 40 nodes and 80 edges.
- Graph tools have `effect=read`; neighbors depth ≤2, paths maxHops ≤4; tool loop ≤4 steps.
- Preserve authorization, publication checks and existing graph context statuses.
- Claims citing a relation require original-text evidence; inferred edges without evidence cannot stand alone.
- Preserve TAP AI/TAP runtime boundaries and the complete `/prototype` design baseline.

---

### Task 1: Retrieval-seeded bounded graph expansion

**Files:** Create `apps/tap-ai-backend/src/tap/modules/knowledge/application/graph_expansion.py`; modify `graph_enrichment.py`, `knowledge/application/retrieve.py`, `interfaces/http/knowledge_service.py`; test `tests/unit/knowledge/test_graph_expansion.py`.

**Interfaces:** `GraphContextExpander.expand(index: SnapshotIndex, chunk_ids: tuple[str, ...], query: str, *, hops: int) -> GraphSubgraph`; `GraphAnswerEnricher.enrich(scope, source_revision_ids, query, *, chunk_ids=(), hops=1) -> GraphAnswerContext`.

- [x] Write failing tests using a chain of graph nodes whose first edge references the retrieved chunk; assert unrelated query text still seeds those nodes, one/two hops differ, confidence ordering is deterministic and caps hold.
- [x] Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/knowledge/test_graph_expansion.py -q` and confirm missing expander failure.
- [x] Implement chunk fact seed lookup, five text-search supplemental nodes, ascending-degree BFS and descending-confidence edge truncation. Assign R labels in context order and include labels, confidence, locators and snapshot ID. Add `graph.expand` seed/hop/count/truncation span attributes.
- [x] Move expansion to a callback after authorized retrieval so actual hit IDs and S labels are available; retain fail-soft statuses and publication revalidation.
- [x] Re-run expansion and existing graph-enrichment tests.

### Task 2: Relation planning and scoped read tools

**Files:** Modify `chat/domain/answer_plan.py`, `chat/application/plan_answer.py`, `chat/adapters/model_gateway_planner.py`, `knowledge/application/planned_answer.py`, `ai/application/tool_loop.py`, `ai/application/tool_registry.py`, `interfaces/http/knowledge_service.py`; create `knowledge/application/graph_tools.py`; test `tests/unit/knowledge/test_graph_tools.py`.

**Interfaces:** `GraphTurnTools(scope, index, context)` owns a Turn's authorized immutable index; `neighbors.invoke(scope, arguments)` and `path.invoke(scope, arguments)` return existing `ToolResult`. `AgentToolRuntime.loop(..., graph_tools=())` registers these executors for that Turn only.

- [x] Add failing tests accepting planner intent `relation` and defaulting missing legacy intent to `fact`; assert unauthorized tool scope is rejected and path hop limits are enforced.
- [x] Run planner/tool tests to observe missing behavior.
- [x] Extend planner enum/prompt and authorized execution intent. Implement exact node-ID or unambiguous label resolution, neighbors depth bounds, BFS path hop bound and newly appended stable R labels.
- [x] For relation intent with applied graph context, union platform defaults `{graph.neighbors, graph.path, knowledge.search}` into the effective allowlist and run existing bounded tool loop even without a selected Agent. Keep other turns' allowlists unchanged.
- [x] Re-run planner, tool registry, tool-loop and graph-tool tests.

### Task 3: Grounded relation claims and public contracts

**Files:** Modify `knowledge/ports/models.py`, `knowledge/domain/models.py`, `knowledge/adapters/grounded_output.py`, `knowledge/adapters/litellm.py`, `knowledge/application/retrieve.py`, `knowledge/api.py`, `contracts/http.py`, `interfaces/http/routes/knowledge_graph.py`; test `tests/unit/knowledge/test_grounded_output.py`, `tests/contract/test_graph_http.py`.

**Interfaces:** `GeneratedClaim.relation_labels: tuple[str, ...] = ()`; `RelationCitation` carries citation/snapshot/edge/source/target IDs, labels, relation type, origin, claim index and path edge IDs. Public camelCase `relationCitations` defaults to `[]`. `POST /knowledge/graph/subgraph {snapshotId, edgeIds}` returns existing `GraphSubgraphView` through project-scoped index loading.

- [x] Write failing tests for valid R labels, unknown-R claim filtering, evidence-label completion from referenced edges, inferred relations without evidence rejection, and project-isolated subgraph lookup.
- [x] Run focused tests to confirm missing behavior.
- [x] Extend closed answer schema with optional `relationLabels`; validate against context, retain only valid claims and rebuild the displayed answer. Carry validated R labels to resolved citations and preserve original-text snapshots.
- [x] Add bounded subgraph DTO and route; load snapshot through graph service and return only requested edges and endpoints.
- [x] Re-run grounded output, answer, graph HTTP and public retrieval contracts.

### Task 4: Durable relation citations, SSE and history

**Files:** Create next numbered migration for `chat_relation_citation`; modify `chat/domain/conversations.py`, `chat/adapters/mysql_conversations.py`, `chat/application/conversations.py`, `entrypoints/tapper_generation_worker.py`, `interfaces/http/sse.py`, `contracts/http.py`; test chat persistence/stream/history contracts.

**Interfaces:** `AnswerEvidence.relation_citations` stores immutable relation values; checkpoints serialize `relationCitations`; SSE emits `relation_citation.resolved` with `{citation}`; `ConversationTurnSummary.relationCitations` restores persisted chips.

- [x] Write failing persistence/checkpoint/SSE tests preserving claim indices and ordered path edge IDs.
- [x] Run focused tests and confirm missing fields/events.
- [x] Store citation rows in the existing finalization transaction, bind their values into evidence digests, restore with legacy-empty defaults, and expose them in history and streaming.
- [x] Re-run conversation domain, MySQL snapshot, worker, history and SSE tests.

### Task 5: Relation golden regression and integration validation

**Files:** Modify `tests/quality/golden/kb-golden.v1.jsonl`, golden corpus fixtures, `src/tap/quality/golden.py`, golden baseline and quality tests; update `docs/architecture.md` with actual implemented behavior.

**Interfaces:** `category=relation`, `expect.relations` and `relationRecall` supplement existing quality metrics; deterministic extraction builds real in-memory snapshots consumed by production answer orchestration.

- [x] Add eight failing relation cases with explicit relation types and deterministic corpus entities.
- [x] Run `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/quality/test_golden_regression.py -q`.
- [x] Build fixture snapshots, generate deterministic relation labels and compute relation recall from returned relation citations; regenerate baseline using runner's existing command.
- [x] Run focused backend checks, `git diff --check`, then parent-owned `make check`, `make test`, browser/prototype/E2E validation. Record actual results and remaining business validation limitations without declaring unrun gates passed.


## Concrete integration and verification notes

The graph callback is evaluated after authorized retrieval, before the bounded tool phase. Its mutable facts list is shared only inside one Turn, allowing tool results to append stable R labels without replacing initial labels:

```python
async def graph_phase(evidence):
    context = await enricher.enrich(
        scope, source_revision_ids, question,
        chunk_ids=tuple(item.chunk_id for item in evidence),
        evidence_labels={item.chunk_id: item.evidence_label for item in evidence},
        hops=2 if execution.intent == "relation" else 1,
    )
    return [dict(fact) for fact in context.facts]

response = await knowledge.answer_frozen(
    request, policy, governance=governance,
    graph_phase=graph_phase, tool_phase=tool_phase,
)
```

Tool executors require both lease/current-Turn authorization and a graph publication callback. `GraphAnswerEnricher.authorize_facts(scope, index, edge_ids)` checks the edge and endpoint evidence against snapshot scope, reopens selected publication authority, authorizes every inventory item and revalidates the publication binding. `GraphTurnTools.resolve` stages new facts and commits them only after the post-search authorization succeeds.

```python
async def authorize_graph(edge_ids):
    await enricher.authorize_facts(scope, context.index, edge_ids)

tools = GraphTurnTools(
    scope, context.index, facts,
    search=authorized_search,
    authorize=reauthorize_turn,
    authorize_graph=authorize_graph,
)
```

`chat_relation_citation.original_citation_ids` preserves the original-text citation identities associated with each `claim_index`; finalization validates their frozen authority and persisted claim text before inserting relation rows. The final answer is rebuilt after filtering relation claims, so its paragraph indices equal the persisted claim indices. Edge sequences track traversal endpoints rather than pairwise intersection, splitting branching relations into continuous paths.

Narrow verification commands:

```sh
uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/contract/test_graph_relation_answer.py apps/tap-ai-backend/tests/unit/knowledge/test_graph_tools.py -q
TAP_RUN_MYSQL_INTEGRATION=1 uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_relation_citation_persistence.py -q
uv run --project apps/tap-ai-backend mypy apps/tap-ai-backend/src/tap
uv run --project apps/tap-ai-backend ruff check apps/tap-ai-backend/src apps/tap-ai-backend/tests apps/tap-ai-backend/migrations/versions/0034_relation_citations.py
git diff --check
```

最终验收：`make check`、完整 `make test`、完整隔离 `make demo-e2e` 通过。步骤由本轮综合交付提交统一收束；真实资料的业务验证不在这些勾选中。
