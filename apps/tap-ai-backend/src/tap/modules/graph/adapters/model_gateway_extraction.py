"""Schema-locked Graph extraction through the shared ModelGateway."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from tap.modules.ai.domain.models import (
    ModelOperation,
    ModelOutputTruncated,
    ModelRequest,
    schema_digest,
    text_digest,
)
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.graph.domain.extraction import GraphExtractionRequest, GraphExtractionTruncated
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshotDraft,
    InferenceProvenance,
    RelationOrigin,
)
from tap.modules.graph.domain.naming import (
    ALIAS_LENGTH_MAX,
    is_reference_name,
    usable_alias,
)
from tap.modules.graph.domain.vocabulary import (
    NODE_ALIAS_MAX,
    NODE_TYPES,
    RELATION_LABEL_MAX,
    RELATION_TYPES,
    normalize_key,
)
from tap.platform.telemetry import span

# Per-batch output caps. One node costs about 35-60 output tokens and one edge
# about 45-60 (compact JSON, Chinese labels; measured on qwen-plus truncations of
# 142 nodes or 125+ edges in 8192 tokens), so 40 nodes + 60 edges stay near 6000
# tokens, inside the 8192-token default budget with room for provenance. A
# 3000-character batch of dense insurance clauses grounds roughly that many
# distinct facts; a batch that needs more is split in two (see the worker).
GRAPH_BATCH_MAX_NODES = 40
GRAPH_BATCH_MAX_EDGES = 60
_CHUNK_REFS: dict[str, object] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Refs (c1, c2, ...) of the input chunks that state this fact.",
}
GRAPH_EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges", "provenance"],
    "properties": {
        "nodes": {
            "type": "array",
            "maxItems": GRAPH_BATCH_MAX_NODES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "type", "canonicalKey", "chunks", "aliases"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": sorted(NODE_TYPES),
                        "description": (
                            "Use ACTOR for people, roles, or organizations -- including an "
                            "insurance company or institution (保险公司, 某保险股份有限公司) -- "
                            "SYSTEM only for a named software application or information "
                            "service, never an organization or an insurance product, "
                            "REQUIREMENT for requirements or controls, CONCEPT for abstract "
                            "ideas including an insurance product or contract (重大疾病保险, "
                            "保险合同), and ENTITY only when none of the specific types "
                            "applies."
                        ),
                    },
                    "canonicalKey": {"type": "string"},
                    "chunks": _CHUNK_REFS,
                    # No maxItems: the gateway would reject a sixth alias outright;
                    # ``_sanitize_aliases`` keeps the first NODE_ALIAS_MAX instead.
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Other names of this entity, up to five, each at most "
                            f"{ALIAS_LENGTH_MAX} characters, naming the entity itself; "
                            "never a pronoun, a reference such as this "
                            "contract or the company, or a clause or section number."
                        ),
                    },
                },
            },
        },
        "edges": {
            "type": "array",
            "maxItems": GRAPH_BATCH_MAX_EDGES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "sourceNodeId",
                    "targetNodeId",
                    "relationType",
                    "relationLabel",
                    "origin",
                    "confidence",
                    "chunks",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "sourceNodeId": {"type": "string"},
                    "targetNodeId": {"type": "string"},
                    "relationType": {
                        "type": "string",
                        "enum": sorted(RELATION_TYPES),
                        "description": (
                            "REQUIRES: the target is jointly necessary for the source (all "
                            "stated REQUIRES conditions must hold), e.g. 重度黄疸 REQUIRES "
                            "亚急性重症肝炎诊断; never use it for a from-N-choose-M threshold "
                            "(下列N项中满足M项) -- use APPLIES_TO for that instead, with the "
                            "threshold as the source and the entity or diagnosis it qualifies "
                            "as the target (the reverse of how REQUIRES would have been "
                            "written, since REQUIRES puts the entity first). "
                            "PART_OF: the source is a sub-clause, sub-item, or finer grade "
                            "of the target, its parent or whole; direction is always the "
                            "more specific thing to the more general one, e.g. Ⅱ级 PART_OF "
                            "心功能状态分级 (not the reverse). Use PART_OF, not EXCEPTION_OF, "
                            "when the text lists what is 包括/含 (included), e.g. 肥厚型心肌病 "
                            "PART_OF 心肌病. "
                            "EXCEPTION_OF: the source is excluded from, or an exception "
                            "carved out of, the target's coverage or rule, e.g. 责任免除事项 "
                            "EXCEPTION_OF 保险责任; never use it for an inclusion (包括/含) -- "
                            "that is PART_OF. "
                            "APPLIES_TO: a stated rule, condition, or threshold (including an "
                            "N-choose-M threshold) names the entity or population it governs; "
                            "the rule/condition/threshold is always the source and that entity "
                            "or population the target, e.g. 犹豫期 APPLIES_TO 新单客户, or "
                            "六项基本日常生活活动中两项 APPLIES_TO 永久性神经系统功能中度障碍 for "
                            "a threshold that qualifies a diagnosis. Use APPLIES_TO only when "
                            "the text itself states the rule governs that entity -- never for "
                            "two entities that merely appear near each other. Do not use "
                            "APPLIES_TO for a diagnosis or event bringing about a payout or "
                            "other consequence -- that is TRIGGERS, even when the diagnosis "
                            "is itself defined by an N-choose-M threshold elsewhere. "
                            "DEFINES: the source (an insurer, contract, or clause) gives the "
                            "target its defined meaning, typically stated with 是指/称为/指 or "
                            "本合同所称, e.g. 重大疾病保险合同条款 DEFINES 重度疾病, or 到达年龄 "
                            "DEFINES 原始投保年龄加保单年度数减一后的年龄 when a clause spells "
                            "out what a term means. Use DEFINES whenever the text explicitly "
                            "glosses a term this way, even if the gloss is short. "
                            "TRIGGERS: the source event, diagnosis, or condition brings about "
                            "the target consequence, benefit, or payout, e.g. 确诊重度疾病 "
                            "TRIGGERS 豁免保险费, or 一至三级伤残 TRIGGERS 护理保险金 when "
                            "reaching a disability grade causes a benefit to be paid. Prefer "
                            "TRIGGERS over APPLIES_TO whenever the text says the source causes, "
                            "brings about, or results in the target being paid, waived, or "
                            "started -- APPLIES_TO is for a rule governing who or what it "
                            "covers, not for the causal event-to-consequence link itself. "
                            "RESPONSIBLE_FOR: the source actor bears the target duty or "
                            "action, e.g. 保险公司 RESPONSIBLE_FOR 合同内容的变更. "
                            "CONFLICTS_WITH: the source and target are stated as mutually "
                            "exclusive alternatives (e.g. a documented three-way choice); "
                            "connect the alternatives to each other, not to the coverage or "
                            "outcome they lead to. "
                            "SUPERSEDES: the source provision replaces the target earlier "
                            "provision. PRECEDES: the source step or stage happens before "
                            "the target in a process. USES: the source actor or process "
                            "uses the target system or resource. VALIDATED_BY: the target "
                            "validates or confirms the source fact. "
                            "RELATED_TO: use only when the text states an association that "
                            "fits none of the above; when unsure, prefer a more specific "
                            "type over RELATED_TO."
                        ),
                    },
                    "relationLabel": {"type": "string"},
                    "origin": {
                        "type": "string",
                        "enum": ["EXTRACTED", "INFERRED"],
                    },
                    "confidence": {"type": "number"},
                    "chunks": _CHUNK_REFS,
                },
            },
        },
        "provenance": {
            "type": "array",
            "maxItems": GRAPH_BATCH_MAX_EDGES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "edgeId", "inputFactIds", "ruleDigest"],
                "properties": {
                    "id": {"type": "string"},
                    "edgeId": {"type": "string"},
                    "inputFactIds": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "ruleDigest": {"type": "string"},
                },
            },
        },
    },
}
GRAPH_EXTRACTION_PROMPT = (
    "Extract only grounded graph facts from the supplied chunks and return exactly the JSON "
    "schema as compact JSON without indentation. "
    f"Output at most {GRAPH_BATCH_MAX_NODES} nodes and at most {GRAPH_BATCH_MAX_EDGES} edges, "
    "keeping the most important facts when there are more. Output only entities and relations "
    "stated in this batch's chunks. Output each node once and at most one edge per source, "
    "relationType and target; never repeat a node or an edge. "
    "Each input chunk carries a short ref (c1, c2, ...). "
    "Cite where a fact is stated only by listing those refs in its chunks array; never "
    "cite a ref that is not in this input and never copy chunk text, hashes, or anchors. Use "
    "short stable IDs: n1, n2, ... for new nodes, e1, e2, ... for edges, and p1, p2, ... for "
    "provenance. Every node must list at least one chunk ref. "
    "Classify every person, role, or organization -- including an insurance company or "
    "institution -- as ACTOR; classify a named software application or information "
    "service, never an organization or an insurance product, as SYSTEM; classify every "
    "stated requirement or control as REQUIREMENT; classify an insurance product or "
    "contract as CONCEPT. Use ENTITY only if none of ACTOR, SYSTEM, REQUIREMENT, or "
    "CONCEPT applies. Every "
    "EXTRACTED edge must list at least one chunk ref. An INFERRED edge must have an empty "
    "chunks array and exactly one provenance item whose edgeId matches it and whose "
    "inputFactIds are nonempty existing node or edge IDs. Never emit a self-edge; "
    "canonicalize aliases into one node and omit any alias edge that would become a self-edge. "
    "Preserve uppercase relation tokens explicitly declared by the document. Apply any explicit "
    "inference rule declared by the document, copy its declared canonical sha256 rule digest, and "
    "record its input edge IDs as provenance. Do not invent facts: only emit an edge when the "
    "chunks you cite actually state a relation between those two entities, not merely mention "
    "both. Write labels, aliases, "
    "canonical keys, and relation labels in the document's own language and keep them short. "
    "Input also carries documentTitle and knownEntities: entities that earlier batches of "
    "the same document already extracted and that this batch's chunks mention. Never output "
    "a known entity in nodes, and never emit a new node for the same real-world thing; an "
    "edge refers to a known entity by putting its id in sourceNodeId or targetNodeId. "
    "Classify processes, steps, or workflow stages as PROCESS. "
    "Use relationType only from the provided enum, choosing the most specific type the "
    "text supports; use RELATED_TO only for a stated association that fits none of the "
    "other types. PART_OF points from a sub-clause, sub-item, or finer grade to its "
    "parent or whole (the more specific thing to the more general one); use PART_OF, not "
    "EXCEPTION_OF, when the text lists what is 包括/含 (included) rather than carving out "
    "an exception. Use APPLIES_TO, not REQUIRES, for a from-N-choose-M threshold (下列N项"
    "中满足M项) that qualifies what counts as a diagnosis or condition; REQUIRES means "
    "every condition listed is jointly necessary. APPLIES_TO "
    "always puts the rule, condition, or threshold as the source and the entity or "
    "population it applies to as the target -- for a threshold, this is the reverse of "
    "how REQUIRES would have put the entity first. When the text instead says a diagnosis, "
    "event, or condition causes, brings about, or results in a benefit, payout, waiver, or "
    "other consequence being paid or started, use TRIGGERS with the diagnosis/event as the "
    "source and the consequence as the target (e.g. 确诊重度疾病 TRIGGERS 豁免保险费), even "
    "when that diagnosis is itself defined elsewhere by an N-choose-M threshold -- APPLIES_TO "
    "is for what a rule governs, TRIGGERS is for what an event causes. When the text explicitly "
    "glosses what a term means (是指/称为/指, 本合同所称, or a clause that spells out a "
    "definition), use DEFINES with the contract, clause, or insurer as the source and the "
    "defined term as the target, even for a short, one-line gloss (e.g. 到达年龄 is defined by "
    "its own clause: 本条款 DEFINES 到达年龄). Put the "
    "document's own wording for the relation in relationLabel (at most 64 characters). "
    "List up to five aliases for a node "
    "when the text names it differently. A label or alias names the entity itself, never a "
    "pronoun or a reference whose meaning depends on the document (本合同, 本公司, 该产品, "
    "this contract, the company), never a clause, section or schedule number (第十九条, 附表1, "
    "Article 5), and never a number; an alias is a short name, not a sentence."
)
GRAPH_EXTRACTION_PROFILE_DIGEST = text_digest(
    GRAPH_EXTRACTION_PROMPT + "\n" + schema_digest(GRAPH_EXTRACTION_SCHEMA)
)


# One extraction batch is long-form structured output (tens of seconds on a
# hosted model), so it gets its own deadline and completion budget instead of
# the interactive model timeout and the gateway's 2048-token default.
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 240.0
MAX_EXTRACTION_TIMEOUT_SECONDS = 300.0
DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS = 8192
MAX_EXTRACTION_OUTPUT_TOKENS = 32768


class ModelGatewayGraphExtraction:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        timeout_seconds: float = DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
        max_output_tokens: int = DEFAULT_EXTRACTION_MAX_OUTPUT_TOKENS,
    ) -> None:
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_EXTRACTION_TIMEOUT_SECONDS
        ):
            raise ValueError("graph extraction timeout must be between 0 and 300 seconds")
        if (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= MAX_EXTRACTION_OUTPUT_TOKENS
        ):
            raise ValueError("graph extraction output budget is out of bounds")
        self._gateway = gateway
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft | None:
        with span(
            "graph.extract_batch", {"tap.graph.batch_index": request.batch_index}
        ) as current_span:
            refs = _chunk_refs(request)
            # Non-ASCII text stays literal: escaping CJK as \uXXXX roughly
            # triples its token cost and the model then echoes the escapes.
            context = json.dumps(
                {
                    "batchIndex": request.batch_index,
                    "documentTitle": request.document_title,
                    "knownEntities": list(request.known_entities),
                    "chunks": [
                        {"ref": ref, "content": chunk.get("content")} for ref, chunk in refs.items()
                    ],
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            try:
                result = await self._gateway.generate_structured(
                    ModelRequest(
                        scope=request.scope,
                        alias=request.model_alias,
                        operation=ModelOperation.STRUCTURED,
                        prompt=GRAPH_EXTRACTION_PROMPT,
                        prompt_digest=text_digest(GRAPH_EXTRACTION_PROMPT),
                        context=context,
                        timeout_seconds=self._timeout_seconds,
                        idempotency_key=request.idempotency_key,
                        schema=GRAPH_EXTRACTION_SCHEMA,
                        schema_digest=schema_digest(GRAPH_EXTRACTION_SCHEMA),
                        max_output_tokens=self._max_output_tokens,
                    )
                )
            except ModelOutputTruncated as error:
                # Cut off at the token budget or past the schema caps: the same
                # input would truncate again, so the worker splits the batch.
                raise GraphExtractionTruncated() from error
            if not isinstance(result.output, dict):
                raise ValueError("graph extraction output must be an object")
            output = cast(dict[str, object], result.output)
            if set(output) != {"nodes", "edges", "provenance"}:
                raise ValueError("graph extraction output has invalid fields")
            try:
                nodes_raw = _objects(output["nodes"], "nodes")
                edges_raw = _objects(output["edges"], "edges")
                provenance_raw = _objects(output["provenance"], "provenance")
                nodes_raw, node_dangling = _strip_dangling_refs(nodes_raw, refs)
                edges_raw, edge_dangling = _strip_dangling_refs(edges_raw, refs)
                dangling_refs = node_dangling + edge_dangling
                snapshot_id = request.snapshot.snapshot_id
                known = _known_entities(request)
                known_id_by_key = {
                    normalize_key(entity["canonicalKey"]): entity_id
                    for entity_id, entity in known.items()
                }
                node_id_remap: dict[str, str] = {}
                known_refs: dict[str, list[str]] = {}
                new_nodes: dict[str, GraphNode] = {}
                new_id_by_key: dict[str, str] = {}
                repeated_known = 0
                duplicate_nodes = 0
                rejected_names = 0
                dropped_no_evidence = 0
                for item in nodes_raw:
                    label = _string(item, "label")
                    canonical_key = _string(item, "canonicalKey")
                    aliases = _sanitize_aliases(_optional_strings(item, "aliases"))
                    if is_reference_name(label):
                        # Named only by a reference ("本公司", "第十九条"): use its
                        # first real alias as the name, or drop the node and its edges.
                        if not aliases:
                            rejected_names += 1
                            continue
                        label, aliases = aliases[0], aliases[1:]
                    if is_reference_name(canonical_key):
                        canonical_key = label
                    node = GraphNode(
                        _string(item, "id"),
                        snapshot_id,
                        label,
                        _string(item, "type"),
                        canonical_key,
                        _cited_refs(item, refs),
                        aliases,
                    )
                    known_id = (
                        node.node_id
                        if node.node_id in known
                        else known_id_by_key.get(normalize_key(node.canonical_key))
                    )
                    if known_id is not None:
                        # A known entity repeated in nodes: drop the copy, keep its
                        # citations, and point its edges at the known id. This
                        # remap must happen before the no-evidence check below, or
                        # an edge in this batch that cites the copy's local id would
                        # wrongly lose its endpoint instead of following the remap.
                        repeated_known += 1
                        node_id_remap[node.node_id] = known_id
                        known_refs.setdefault(known_id, []).extend(node.evidence_ids)
                        continue
                    key = normalize_key(node.canonical_key)
                    survivor_id = (
                        node.node_id if node.node_id in new_nodes else new_id_by_key.get(key)
                    )
                    if survivor_id is not None:
                        # The same node twice (same id, or same canonical key under
                        # another id): keep the first, union citations and aliases
                        # (same ordering reason as the known-entity remap above).
                        duplicate_nodes += 1
                        if node.node_id != survivor_id:
                            node_id_remap[node.node_id] = survivor_id
                        survivor = new_nodes[survivor_id]
                        new_nodes[survivor_id] = replace(
                            survivor,
                            evidence_ids=tuple(
                                dict.fromkeys((*survivor.evidence_ids, *node.evidence_ids))
                            ),
                            aliases=_sanitize_aliases((*survivor.aliases, *node.aliases)),
                        )
                        continue
                    if not node.evidence_ids and item.get(_HAD_REFS_BEFORE_STRIP):
                        # A genuinely new node: every ref it cited was outside the
                        # batch and was stripped above, so no evidence survives for
                        # this fact. Drop it rather than fail the whole batch; its
                        # edges follow once this id disappears from ``node_ids``
                        # below. A node the model emitted with an already-empty
                        # ``chunks`` (the flag unset) keeps its previous behavior
                        # instead -- kept, with no evidence.
                        dropped_no_evidence += 1
                        continue
                    new_nodes[node.node_id] = node
                    new_id_by_key.setdefault(key, node.node_id)
                edges_raw = tuple(_remap_endpoints(item, node_id_remap) for item in edges_raw)
                provenance_raw = tuple(
                    _remap_input_facts(item, node_id_remap) for item in provenance_raw
                )
                edges_raw, provenance_raw, duplicate_edges = _dedupe_edges(
                    edges_raw, provenance_raw
                )
                node_ids = set(new_nodes) | set(known)
                kept_edges_raw, kept_provenance_raw, dropped, dropped_no_evidence_edges = (
                    _keep_edges(edges_raw, provenance_raw, node_ids)
                )
                label_lookup = {
                    node_id: (node.label, node.aliases, node.node_type)
                    for node_id, node in new_nodes.items()
                } | {
                    entity_id: (entity["label"], (), entity["type"])
                    for entity_id, entity in known.items()
                }
                kept_edges_raw, kept_provenance_raw, dropped_unsupported_edges = (
                    _drop_unsupported_edges(kept_edges_raw, kept_provenance_raw, label_lookup, refs)
                )
                edges = tuple(
                    GraphEdge(
                        _string(item, "id"),
                        snapshot_id,
                        _string(item, "sourceNodeId"),
                        _string(item, "targetNodeId"),
                        _string(item, "relationType"),
                        RelationOrigin(_string(item, "origin")),
                        _float(item, "confidence"),
                        _cited_refs(item, refs),
                        _string(item, "relationLabel")[:RELATION_LABEL_MAX],
                    )
                    for item in kept_edges_raw
                )
                nodes = (
                    *_referenced_known_nodes(snapshot_id, known, known_refs, edges),
                    *new_nodes.values(),
                )
                provenance = tuple(
                    InferenceProvenance(
                        _string(item, "id"),
                        snapshot_id,
                        _string(item, "edgeId"),
                        _strings(item, "inputFactIds"),
                        _string(item, "ruleDigest"),
                    )
                    for item in kept_provenance_raw
                )
                evidence = _backfill_evidence(snapshot_id, refs, nodes, edges)
            except (KeyError, TypeError) as error:
                raise ValueError("graph extraction output is malformed") from error
            current_span.set_attribute("tap.graph.dropped_edges", dropped)
            current_span.set_attribute("tap.graph.repeated_known_entities", repeated_known)
            current_span.set_attribute("tap.graph.duplicate_nodes", duplicate_nodes)
            current_span.set_attribute("tap.graph.duplicate_edges", duplicate_edges)
            current_span.set_attribute("tap.graph.rejected_names", rejected_names)
            current_span.set_attribute("tap.graph.dangling_refs", dangling_refs)
            current_span.set_attribute("tap.graph.dropped_no_evidence_nodes", dropped_no_evidence)
            current_span.set_attribute(
                "tap.graph.dropped_no_evidence_edges", dropped_no_evidence_edges
            )
            current_span.set_attribute(
                "tap.graph.dropped_unsupported_edges", dropped_unsupported_edges
            )
        if not nodes:
            # The model grounded nothing in this batch (boilerplate chunks, no
            # facts to extract): a successful batch with nothing to contribute,
            # not a failure. ``GraphSnapshotDraft`` requires at least one node, so
            # there is no way to represent "empty" as a draft; the worker treats
            # ``None`` as this batch's signal and skips it at assembly.
            return None
        return GraphSnapshotDraft(request.snapshot, nodes, edges, evidence, provenance)


def _known_entities(request: GraphExtractionRequest) -> dict[str, Mapping[str, str]]:
    """The request's known entities by id (string fields only; others are ignored)."""

    known: dict[str, Mapping[str, str]] = {}
    for entity in request.known_entities:
        fields = {key: entity.get(key) for key in ("id", "label", "type", "canonicalKey")}
        if all(isinstance(value, str) and value for value in fields.values()):
            known[str(fields["id"])] = cast(dict[str, str], fields)
    return known


def _remap_endpoints(item: dict[str, object], remap: Mapping[str, str]) -> dict[str, object]:
    source, target = item.get("sourceNodeId"), item.get("targetNodeId")
    if source not in remap and target not in remap:
        return item
    return item | {
        "sourceNodeId": remap.get(source, source) if isinstance(source, str) else source,
        "targetNodeId": remap.get(target, target) if isinstance(target, str) else target,
    }


def _remap_input_facts(item: dict[str, object], remap: Mapping[str, str]) -> dict[str, object]:
    facts = item.get("inputFactIds")
    if not isinstance(facts, list) or not any(fact in remap for fact in facts):
        return item
    remapped = [remap.get(fact, fact) if isinstance(fact, str) else fact for fact in facts]
    return item | {"inputFactIds": list(dict.fromkeys(remapped))}


def _referenced_known_nodes(
    snapshot_id: str,
    known: Mapping[str, Mapping[str, str]],
    known_refs: Mapping[str, list[str]],
    edges: tuple[GraphEdge, ...],
) -> tuple[GraphNode, ...]:
    """Server-built nodes for the known entities this batch's edges reference.

    Label, type and canonical key come from the request (the entity an earlier batch
    minted); evidence is every chunk ref a repeated copy or a touching edge cited.
    """

    refs: dict[str, list[str]] = {}
    for edge in edges:
        for endpoint in (edge.source_node_id, edge.target_node_id):
            if endpoint in known:
                refs.setdefault(endpoint, list(known_refs.get(endpoint, ()))).extend(
                    edge.evidence_ids
                )
    return tuple(
        GraphNode(
            entity_id,
            snapshot_id,
            known[entity_id]["label"],
            known[entity_id]["type"],
            known[entity_id]["canonicalKey"],
            tuple(dict.fromkeys(cited)),
        )
        for entity_id, cited in refs.items()
    )


def _chunk_refs(request: GraphExtractionRequest) -> dict[str, Mapping[str, object]]:
    """Batch-local short refs (``c1``..``cN``, in input order) for the request chunks."""

    return {f"c{index}": chunk for index, chunk in enumerate(request.chunks, start=1)}


_HAD_REFS_BEFORE_STRIP = "_hadRefsBeforeStrip"


def _strip_dangling_refs(
    items: tuple[dict[str, object], ...], refs: Mapping[str, Mapping[str, object]]
) -> tuple[tuple[dict[str, object], ...], int]:
    """Drop chunk refs outside this batch from each item's ``chunks`` field.

    The model occasionally cites a ref from outside the current batch (for example a
    stale ``c12`` when only ``c1``-``c9`` were sent this batch); retrying the same
    input reproduces it, so failing the whole batch wastes the retry budget. A
    dangling ref is dropped instead, here, before any fact is built from ``items``.

    Each cleaned item also carries a transient ``_hadRefsBeforeStrip`` flag -- not a
    schema field, and ignored by every downstream lookup that reads a specific key --
    recording whether its original ``chunks`` was nonempty. A caller that finds a
    node or edge left with no evidence after this must drop it only when that flag
    is set: a fact that became evidence-less *because* a dangling ref was stripped
    here is dropped silently (see the node loop and ``_keep_edges`` in ``extract``),
    but a fact the model emitted with an already-empty ``chunks`` keeps its previous
    behavior (a node is still kept with no evidence; an EXTRACTED edge still fails
    the batch closed when ``GraphEdge`` rejects it for having no evidence). The flag
    survives ``_remap_endpoints`` (a plain dict union keeps extra keys) and is OR-ed
    together by ``_dedupe_edges`` when two raw edges under the same id/shape merge.
    Returns the cleaned items and how many refs were dropped in total.
    """

    cleaned: list[dict[str, object]] = []
    dangling = 0
    for item in items:
        chunks = _strings(item, "chunks")
        kept = tuple(dict.fromkeys(ref for ref in chunks if ref in refs))
        dangling += len(chunks) - len(kept)
        patch: dict[str, object] = {_HAD_REFS_BEFORE_STRIP: bool(chunks)}
        if len(kept) != len(chunks):
            patch["chunks"] = list(kept)
        cleaned.append(item | patch)
    return tuple(cleaned), dangling


def _cited_refs(
    value: dict[str, object], refs: Mapping[str, Mapping[str, object]]
) -> tuple[str, ...]:
    """The chunk refs a fact cites, deduplicated in first-cited order.

    Callers run this only after ``_strip_dangling_refs`` has removed any ref outside
    this batch, so the ``ValueError`` below is a defensive invariant, not the normal
    path for a dangling ref.
    """

    cited: list[str] = []
    for ref in _strings(value, "chunks"):
        if ref not in refs:
            raise ValueError("graph extraction cites a dangling chunk ref")
        if ref not in cited:
            cited.append(ref)
    return tuple(cited)


def _backfill_evidence(
    snapshot_id: str,
    refs: Mapping[str, Mapping[str, object]],
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
) -> tuple[Evidence, ...]:
    """Build one evidence record per cited chunk from the authorized request chunk.

    Revision ids, chunk id, anchor and content digest come from the request, never
    from model output, so they always resolve to a chunk this batch was given. The
    evidence id is the chunk ref itself; the worker namespaces it per batch.
    """

    cited = {ref for node in nodes for ref in node.evidence_ids} | {
        ref for edge in edges for ref in edge.evidence_ids
    }
    evidence: list[Evidence] = []
    for ref, chunk in refs.items():
        if ref not in cited:
            continue
        anchor = chunk.get("anchor")
        if not isinstance(anchor, Mapping):
            raise TypeError("anchor")
        evidence.append(
            Evidence(
                ref,
                snapshot_id,
                _chunk_string(chunk, "sourceRevisionId"),
                _chunk_string(chunk, "documentRevisionId"),
                _chunk_string(chunk, "chunkId"),
                dict(anchor),
                _chunk_string(chunk, "contentDigest"),
            )
        )
    return tuple(evidence)


def _chunk_string(chunk: Mapping[str, object], key: str) -> str:
    item = chunk.get(key)
    if not isinstance(item, str):
        raise TypeError(key)
    return item


def _keep_edges(
    edges_raw: tuple[dict[str, object], ...],
    provenance_raw: tuple[dict[str, object], ...],
    node_ids: set[str],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...], int, int]:
    """Drop edges outside the vocabulary or batch, then cascade to their provenance.

    An edge is dropped when its relationType is outside RELATION_TYPES, either endpoint
    is not among this batch's node ids, both endpoints are the same node (e.g. after a
    duplicate node was merged into the other endpoint), or it is EXTRACTED, left with no
    chunk refs, and ``_HAD_REFS_BEFORE_STRIP`` is set -- meaning every ref it cited was
    outside the batch and was stripped by ``_strip_dangling_refs`` upstream, so no
    evidence survives for the fact and the edge is dropped rather than failing the whole
    batch. An EXTRACTED edge the model emitted with an already-empty ``chunks`` (the flag
    unset) is *not* dropped here: it keeps its previous behavior of failing the batch
    closed when ``GraphEdge`` rejects it for having no evidence. An INFERRED edge's
    chunks are legitimately empty by design and is never dropped on either basis.
    Dropping an edge also drops the provenance record that cites it (by edgeId), and any
    INFERRED edge whose provenance lists a dropped edge id as an input fact is dropped in
    turn, repeating until no further edge is dropped. Returns the kept edges, the kept
    provenance, the total number of edges dropped, and how many of those were dropped
    specifically for the EXTRACTED-with-stripped-evidence reason above (counted
    separately so it is distinguishable from the other drop reasons on the span).
    """
    provenance_by_edge: dict[object, dict[str, object]] = {
        item.get("edgeId"): item for item in provenance_raw
    }
    no_evidence_ids: set[object] = {
        item.get("id")
        for item in edges_raw
        if item.get("origin") == "EXTRACTED"
        and not item.get("chunks")
        and item.get(_HAD_REFS_BEFORE_STRIP)
    }
    dropped_ids: set[object] = no_evidence_ids | {
        item.get("id")
        for item in edges_raw
        if item.get("relationType") not in RELATION_TYPES
        or item.get("sourceNodeId") not in node_ids
        or item.get("targetNodeId") not in node_ids
        or item.get("sourceNodeId") == item.get("targetNodeId")
    }
    changed = True
    while changed:
        changed = False
        for item in edges_raw:
            edge_id = item.get("id")
            if edge_id in dropped_ids or item.get("origin") != "INFERRED":
                continue
            provenance_item = provenance_by_edge.get(edge_id)
            input_facts = provenance_item.get("inputFactIds") if provenance_item else None
            if isinstance(input_facts, list) and dropped_ids.intersection(input_facts):
                dropped_ids.add(edge_id)
                changed = True
    kept_edges = tuple(item for item in edges_raw if item.get("id") not in dropped_ids)
    kept_provenance = tuple(
        item for item in provenance_raw if item.get("edgeId") not in dropped_ids
    )
    return kept_edges, kept_provenance, len(dropped_ids), len(no_evidence_ids)


_UNCHECKED_ENDPOINT_TYPES = frozenset({"ACTOR", "SYSTEM"})
# A real, grounded-but-paraphrased endpoint keeps at least this fraction of its
# characters in the cited text (measured empirically: the lowest-scoring genuine
# paraphrase found in a production replay audit was 0.93; every true adjacent-entry
# mislink in that same audit scored 0.0-0.33). 0.6 keeps a wide margin above the
# former and well above the latter.
_UNSUPPORTED_EDGE_OVERLAP_MIN = 0.6


def _char_overlap_ratio(label: str, text: str) -> float:
    """The fraction of ``label``'s normalized characters that also occur in ``text``.

    A multiset comparison (via ``collections.Counter``), not a substring check: order
    and contiguity do not matter, only how many of the label's characters the text
    also contains (each text character usable once per matching label character).
    An empty, already-normalized label (e.g. punctuation-only) trivially scores 1.0
    rather than dividing by zero -- there is nothing for the text to be missing.
    """

    from collections import Counter

    label_chars = list(label)
    if not label_chars:
        return 1.0
    text_counts = Counter(text)
    label_counts = Counter(label_chars)
    matched = sum(min(count, text_counts.get(char, 0)) for char, count in label_counts.items())
    return matched / len(label_chars)


_NUMBERED_ITEM_MARKER = re.compile(r"(?:^|\n)\s*\d{1,3}）")
_ITEM_COOCCURRENCE_LCS_MIN = 0.5


def _numbered_items(text: str) -> list[str]:
    """Split ``text`` on a bare top-level numbered-item marker (37）, 38）, ...).

    Insurance clause lists in this corpus number each top-level entry (a disease
    definition, a responsibility, a termination condition) as ``NN）`` at the start
    of a line, then number that entry's own sub-criteria as ``（N）`` -- with a
    leading full-width paren the marker above deliberately excludes, so a disease's
    own criteria stay in its item rather than becoming separate items. A chunk with
    no bare marker returns the whole text as one item (the per-endpoint overlap
    check below already covers that ungrouped case).
    """
    bounds = [match.start() for match in _NUMBERED_ITEM_MARKER.finditer(text)]
    if not bounds:
        return [text]
    edges = sorted({0, *bounds, len(text)})
    return [
        text[edges[index] : edges[index + 1]]
        for index in range(len(edges) - 1)
        if text[edges[index] : edges[index + 1]].strip()
    ]


def _longest_common_substring_len(first: str, second: str) -> int:
    """The length of the longest contiguous run shared by ``first`` and ``second``.

    Unlike ``_char_overlap_ratio``'s multiset comparison, contiguity matters here:
    two disease names built from the same common morphemes (严重, 性, 炎, ...) but
    naming different diseases share many characters in any order, which is exactly
    what lets a cross-item mislink slip past a per-character check scoped to one
    item's text. A long shared *run* is far more specific, since it requires the
    label's words to appear together, in order, not just individually.
    """
    if not first or not second:
        return 0
    previous_row = [0] * (len(second) + 1)
    longest = 0
    for first_char in first:
        current_row = [0] * (len(second) + 1)
        for column, second_char in enumerate(second, start=1):
            if first_char == second_char:
                current_row[column] = previous_row[column - 1] + 1
                longest = max(longest, current_row[column])
        previous_row = current_row
    return longest


def _edge_endpoints_share_an_item(
    source_names: tuple[str, ...], target_names: tuple[str, ...], text: str
) -> bool:
    """Whether some single numbered item names both endpoints well enough.

    Scoped to text with at least two numbered items (a genuine dense list, the
    rv3 adjacent-entry mislink setting): an edge is kept only if there exists one
    item where the best-matching name of *each* endpoint reaches
    ``_ITEM_COOCCURRENCE_LCS_MIN`` of its own length as a longest common substring
    with that item's text. Text with fewer than two items returns ``True``
    unconditionally -- ``_drop_unsupported_edges``'s whole-text overlap check
    already covers an edge with no (or only one) numbered item to localize within.
    """
    items = _numbered_items(text)
    if len(items) < 2:
        return True
    for item in items:
        normalized_item = normalize_key(item)

        def best_ratio(names: tuple[str, ...]) -> float:
            return max(
                (
                    _longest_common_substring_len(normalize_key(name), normalized_item)
                    / len(normalize_key(name))
                    for name in names
                    if name and normalize_key(name)
                ),
                default=1.0,
            )

        if (
            best_ratio(source_names) >= _ITEM_COOCCURRENCE_LCS_MIN
            and best_ratio(target_names) >= _ITEM_COOCCURRENCE_LCS_MIN
        ):
            return True
    return False


def _drop_unsupported_edges(
    edges_raw: tuple[dict[str, object], ...],
    provenance_raw: tuple[dict[str, object], ...],
    label_lookup: Mapping[str, tuple[str, tuple[str, ...], str]],
    refs: Mapping[str, Mapping[str, object]],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...], int]:
    """Drop an EXTRACTED edge whose endpoints are not both named in its own evidence.

    The model cites which chunks state each fact; a genuinely grounded edge's
    endpoint name shares nearly all of its characters with that text, even when
    the model paraphrases a multi-clause bullet (dropping a middle clause,
    rewording "少于1%" as "<1%", or dropping a trailing "术"/"的" the clause
    itself omits). An edge whose endpoint shares *none* of its characters with
    the cited text is instead almost always a real rv3 failure mode: two
    adjacent clause entries (e.g. two neighboring disease definitions) connected
    to each other instead of to what the cited text actually relates. So this
    checks, per endpoint, the fraction of the label's (or its best-matching
    alias's) characters -- normalized the same way ``canonical_key`` is -- that
    also occur in the cited text (as a multiset, not requiring contiguity or
    order); an edge is dropped only when that fraction is below
    ``_UNSUPPORTED_EDGE_OVERLAP_MIN`` for either endpoint, rather than published
    as a plausible-looking but ungrounded link.

    An ACTOR or SYSTEM endpoint is never checked: the insurer or the contract
    itself is routinely referred to in its own clause only by a document-relative
    pronoun (本公司, 本合同, 本附加合同) -- which ``usable_alias`` deliberately keeps
    out of every node's aliases (see the prompt's alias rule) -- so requiring its
    real name to appear literally would drop a large share of genuinely grounded
    facts about the insurer or the contract itself, not just the adjacent-entry
    mislinks this check targets. An INFERRED edge has no chunks by design (its
    grounding is its provenance's input facts, not text) and is never checked. An
    edge whose endpoint id is not in ``label_lookup``, or whose own ``chunks`` is
    empty, is left to ``_keep_edges`` and ``GraphEdge`` to judge instead of being
    dropped here. Dropping an edge cascades to its own provenance record and to
    any INFERRED edge whose provenance names it as an input fact, the same cascade
    ``_keep_edges`` uses. Returns the kept edges, kept provenance, and how many
    edges were dropped.
    """

    def supported(item: dict[str, object]) -> bool:
        if item.get("origin") != "EXTRACTED":
            return True
        chunks = item.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return True
        raw_text = "".join(
            str(refs[ref]["content"]) for ref in chunks if isinstance(ref, str) and ref in refs
        )
        text = normalize_key(raw_text)
        endpoint_names: dict[str, tuple[str, ...]] = {}
        for endpoint_key in ("sourceNodeId", "targetNodeId"):
            endpoint = item.get(endpoint_key)
            names = label_lookup.get(endpoint) if isinstance(endpoint, str) else None
            if names is None:
                endpoint_names[endpoint_key] = ()
                continue
            label, aliases, node_type = names
            if node_type in _UNCHECKED_ENDPOINT_TYPES:
                endpoint_names[endpoint_key] = ()
                continue
            endpoint_names[endpoint_key] = (label, *aliases)
            best = max(
                (
                    _char_overlap_ratio(normalize_key(name), text)
                    for name in (label, *aliases)
                    if name
                ),
                default=1.0,
            )
            if best < _UNSUPPORTED_EDGE_OVERLAP_MIN:
                return False
        # Both endpoints pass the whole-text check above; a dense numbered list
        # (>=2 items) additionally requires some single item to name both -- the
        # whole-text check alone cannot tell "both diseases are somewhere in this
        # list" from "this is the one disease both facts are actually about".
        if endpoint_names["sourceNodeId"] and endpoint_names["targetNodeId"]:
            if not _edge_endpoints_share_an_item(
                endpoint_names["sourceNodeId"], endpoint_names["targetNodeId"], raw_text
            ):
                return False
        return True

    provenance_by_edge: dict[object, dict[str, object]] = {
        item.get("edgeId"): item for item in provenance_raw
    }
    dropped_ids: set[object] = {item.get("id") for item in edges_raw if not supported(item)}
    changed = True
    while changed:
        changed = False
        for item in edges_raw:
            edge_id = item.get("id")
            if edge_id in dropped_ids or item.get("origin") != "INFERRED":
                continue
            provenance_item = provenance_by_edge.get(edge_id)
            input_facts = provenance_item.get("inputFactIds") if provenance_item else None
            if isinstance(input_facts, list) and dropped_ids.intersection(input_facts):
                dropped_ids.add(edge_id)
                changed = True
    kept_edges = tuple(item for item in edges_raw if item.get("id") not in dropped_ids)
    kept_provenance = tuple(
        item for item in provenance_raw if item.get("edgeId") not in dropped_ids
    )
    return kept_edges, kept_provenance, len(dropped_ids)


def _dedupe_edges(
    edges_raw: tuple[dict[str, object], ...],
    provenance_raw: tuple[dict[str, object], ...],
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...], int]:
    """Keep one edge per id and per (source, relationType, target, origin).

    A repeated edge (real outputs looped on one edge until truncated) merges into
    the first: chunk refs union, confidence takes the maximum. Its provenance is
    dropped, inputs citing it point at the survivor, and only the first provenance
    per edge id is kept. Returns the kept edges, provenance and the number dropped.
    """

    kept: dict[object, dict[str, object]] = {}
    survivor_by_shape: dict[tuple[str, ...], object] = {}
    edge_remap: dict[str, str] = {}
    duplicates = 0
    for item in edges_raw:
        edge_id = item.get("id")
        shape = tuple(
            item.get(key) for key in ("sourceNodeId", "relationType", "targetNodeId", "origin")
        )
        if not isinstance(edge_id, str) or not all(isinstance(part, str) for part in shape):
            kept.setdefault(edge_id, item)  # malformed: left for parsing to reject
            continue
        if edge_id in kept:
            duplicates += 1
            continue
        survivor_id = survivor_by_shape.get(cast(tuple[str, ...], shape))
        if survivor_id is None:
            kept[edge_id] = item
            survivor_by_shape[cast(tuple[str, ...], shape)] = edge_id
            continue
        duplicates += 1
        edge_remap[edge_id] = cast(str, survivor_id)
        survivor = kept[survivor_id]
        chunks, extra = survivor.get("chunks"), item.get("chunks")
        confidence, other = survivor.get("confidence"), item.get("confidence")
        merged = dict(survivor)
        if isinstance(chunks, list) and isinstance(extra, list):
            merged["chunks"] = list(dict.fromkeys([*chunks, *extra]))
        if type(confidence) in {int, float} and type(other) in {int, float}:
            merged["confidence"] = max(cast(float, confidence), cast(float, other))
        # Union the "had refs before stripping" flag too: if either raw edge under
        # this id/shape originally cited a ref, the merged edge did -- relevant only
        # when the union above still leaves ``chunks`` empty (see
        # ``_HAD_REFS_BEFORE_STRIP`` in ``_strip_dangling_refs``).
        merged[_HAD_REFS_BEFORE_STRIP] = bool(survivor.get(_HAD_REFS_BEFORE_STRIP)) or bool(
            item.get(_HAD_REFS_BEFORE_STRIP)
        )
        kept[survivor_id] = merged
    provenance: list[dict[str, object]] = []
    claimed: set[object] = set()
    for item in provenance_raw:
        edge_id = item.get("edgeId")
        if edge_id in edge_remap or edge_id in claimed:
            continue
        claimed.add(edge_id)
        facts = item.get("inputFactIds")
        if isinstance(facts, list) and any(fact in edge_remap for fact in facts):
            remapped = [
                edge_remap.get(fact, fact) if isinstance(fact, str) else fact for fact in facts
            ]
            item = item | {"inputFactIds": list(dict.fromkeys(remapped))}
        provenance.append(item)
    return tuple(kept.values()), tuple(provenance), duplicates


def _sanitize_aliases(raw: tuple[str, ...]) -> tuple[str, ...]:
    """Clean model-provided aliases before constructing a ``GraphNode``.

    Strips surrounding whitespace, drops blank entries and any alias ``usable_alias``
    rejects (references, numbering, longer than ``ALIAS_LENGTH_MAX``), dedupes
    case/width-insensitively (via ``normalize_key``, keeping the first-seen
    spelling), and caps the result at ``NODE_ALIAS_MAX``. Without this, a model that
    returns a blank, over-length, or merely differently-cased duplicate alias would
    make ``GraphNode`` construction raise.
    """

    seen: set[str] = set()
    sanitized: list[str] = []
    for alias in raw:
        stripped = alias.strip()
        if not stripped or not usable_alias(stripped):
            continue
        key = normalize_key(stripped)
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(stripped)
        if len(sanitized) >= NODE_ALIAS_MAX:
            break
    return tuple(sanitized)


def _objects(value: object, name: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"graph extraction {name} must be object arrays")
    return tuple(cast(dict[str, object], item) for item in value)


def _string(value: dict[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TypeError(key)
    return item


def _strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    item = value[key]
    if not isinstance(item, list) or any(not isinstance(part, str) for part in item):
        raise TypeError(key)
    return tuple(cast(list[str], item))


def _optional_strings(value: dict[str, object], key: str) -> tuple[str, ...]:
    if key not in value:
        return ()
    return _strings(value, key)


def _float(value: dict[str, object], key: str) -> float:
    item = value[key]
    if type(item) not in {int, float}:
        raise TypeError(key)
    return float(cast(float, item))
