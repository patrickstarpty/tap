"""Rule-based local Graph extraction used ahead of a model-backed extractor."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Mapping

from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.vocabulary import normalize_key

_SENTENCE_SPLIT = re.compile(r"[。；！？.;!?\n]")

_CN_ENTITY = r'[^，,、：:（）()"“”\s]{2,40}'
_EN_ENTITY = r"[A-Za-z][A-Za-z0-9 \-]{1,60}?"

# relation_type -> (chinese triggers, english triggers, swap subject/object)
_TABLE: tuple[tuple[str, tuple[str, ...], tuple[str, ...], bool], ...] = (
    ("REQUIRES", ("需要", "须提供", "必须提供"), ("requires", "require", "must provide"), False),
    ("APPLIES_TO", ("适用于",), ("applies to", "apply to"), False),
    ("PART_OF", ("属于", "包含于"), ("is part of", "belongs to"), False),
    (
        "EXCEPTION_OF",
        ("是…的例外", "除外于"),
        ("is an exception of", "is an exception to"),
        False,
    ),
    ("SUPERSEDES", ("取代", "替代"), ("supersedes", "replaces"), False),
    ("TRIGGERS", ("触发",), ("triggers",), False),
    ("PRECEDES", ("之后是", "然后进入", "下一步是"), ("precedes", "is followed by"), False),
    ("VALIDATED_BY", ("由…验证", "由…核对"), ("is validated by", "is verified by"), False),
    ("RESPONSIBLE_FOR", ("由…负责",), ("is handled by", "is owned by"), True),
    ("USES", ("使用",), ("uses",), False),
    ("DEFINES", ("定义",), ("defines",), False),
    ("CONFLICTS_WITH", ("与…冲突",), ("conflicts with",), False),
)


def _is_english(trigger: str) -> bool:
    return not any(ord(ch) > 127 for ch in trigger)


def _compile(trigger: str) -> re.Pattern[str]:
    # English triggers and entities are bounded with \b so a shorter trigger (or
    # entity) cannot match inside a longer word, e.g. "require" inside "requires"
    # or "requirement", or "uses" inside "causes". \b is meaningless for Chinese
    # (CJK characters are all "word" characters to re, so there is never a
    # transition to anchor on) and is intentionally omitted there; the longest-
    # trigger-first span masking in rule_based_draft handles Chinese overlap
    # instead (e.g. 须提供 is a substring of 必须提供).
    if _is_english(trigger):
        entity = rf"\b(?:{_EN_ENTITY})\b"
        literal = rf"\b{re.escape(trigger)}\b"
    else:
        entity = _CN_ENTITY
        literal = re.escape(trigger)
    if "…" in trigger:
        prefix, suffix = trigger.split("…", 1)
        if _is_english(trigger):
            prefix = rf"\b{re.escape(prefix)}\b"
            suffix = rf"\b{re.escape(suffix)}\b"
        else:
            prefix = re.escape(prefix)
            suffix = re.escape(suffix)
        return re.compile(rf"({entity})\s*{prefix}\s*({entity})\s*{suffix}")
    return re.compile(rf"({entity})\s*{literal}\s*({entity})$")


# Module constant: one entry per trigger variant, carrying the relation type, the
# compiled matcher, the trigger text as written in the rule table (used verbatim as
# the edge's relation_label, never the text actually matched in a sentence) and
# whether the matched subject/object pair must be swapped before forming the edge.
_PATTERNS: tuple[tuple[re.Pattern[str], str, str, bool], ...] = tuple(
    (_compile(trigger), relation_type, trigger, swap)
    for relation_type, cn_triggers, en_triggers, swap in _TABLE
    for trigger in (*cn_triggers, *en_triggers)
)

# Tried longest-trigger-first within a sentence so that, e.g., 必须提供 claims its
# span before the substring trigger 须提供 is tried; a shorter trigger whose match
# falls inside an already-claimed span is skipped (see rule_based_draft).
_PATTERNS_BY_TRIGGER_LENGTH: tuple[tuple[re.Pattern[str], str, str, bool], ...] = tuple(
    sorted(_PATTERNS, key=lambda entry: len(entry[2]), reverse=True)
)

_ACTOR_HINTS = re.compile(
    r"审核|核保员|经办|客户|用户|代理人|reviewer|approver|underwriter|agent|customer|user",
    re.IGNORECASE,
)
_SYSTEM_HINTS = re.compile(r"系统|平台|门户|接口|system|portal|service|api", re.IGNORECASE)
_REQUIREMENT_HINTS = re.compile(
    r"规则|要求|条款|政策|控制|rule|requirement|policy|control", re.IGNORECASE
)
_PROCESS_HINTS = re.compile(r"流程|步骤|环节|阶段|process|flow|step|stage", re.IGNORECASE)

_NODE_CAP = 300
_CHUNK_CAP = 500


def _node_type(label: str) -> str:
    if _ACTOR_HINTS.search(label):
        return "ACTOR"
    if _SYSTEM_HINTS.search(label):
        return "SYSTEM"
    if _REQUIREMENT_HINTS.search(label):
        return "REQUIREMENT"
    if _PROCESS_HINTS.search(label):
        return "PROCESS"
    return "CONCEPT"


def _touch_node(
    nodes_by_key: dict[str, GraphNode],
    node_order: list[str],
    snapshot_id: str,
    label: str,
    key: str,
    evidence_id: str,
) -> GraphNode:
    node = nodes_by_key.get(key)
    if node is None:
        node_id = "grn_" + hashlib.sha256(key.encode()).hexdigest()[:32]
        node = GraphNode(node_id, snapshot_id, label, _node_type(label), key, (evidence_id,))
        node_order.append(key)
    elif evidence_id not in node.evidence_ids:
        node = replace(node, evidence_ids=node.evidence_ids + (evidence_id,))
    nodes_by_key[key] = node
    return node


def rule_based_draft(
    snapshot: GraphSnapshot, chunks: tuple[Mapping[str, object], ...], *, filename: str
) -> GraphSnapshotDraft:
    evidence: list[Evidence] = []
    node_order: list[str] = []
    nodes_by_key: dict[str, GraphNode] = {}
    edge_order: list[str] = []
    edges_by_id: dict[str, GraphEdge] = {}

    for item in chunks[:_CHUNK_CAP]:
        chunk_id = str(item["chunkId"])
        evidence_id = "gre_" + hashlib.sha256(chunk_id.encode()).hexdigest()[:32]
        evidence.append(
            Evidence(
                evidence_id,
                snapshot.snapshot_id,
                str(item["sourceRevisionId"]),
                str(item["documentRevisionId"]),
                chunk_id,
                item["anchor"],  # type: ignore[arg-type]
                str(item["contentDigest"]),
            )
        )
        content = str(item["content"])
        for raw_sentence in _SENTENCE_SPLIT.split(content):
            sentence = raw_sentence.strip()
            if len(sentence) < 4:
                continue
            claimed_spans: list[tuple[int, int]] = []
            for pattern, relation_type, relation_label, swap in _PATTERNS_BY_TRIGGER_LENGTH:
                match = pattern.search(sentence)
                if not match:
                    continue
                match_start, match_end = match.span()
                if any(
                    match_start < claimed_end and claimed_start < match_end
                    for claimed_start, claimed_end in claimed_spans
                ):
                    continue
                claimed_spans.append((match_start, match_end))
                first = match.group(1).strip()
                second = match.group(2).strip()
                first_key = normalize_key(first)
                second_key = normalize_key(second)
                if not first_key or not second_key:
                    continue
                if first_key == second_key:
                    _touch_node(
                        nodes_by_key,
                        node_order,
                        snapshot.snapshot_id,
                        first,
                        first_key,
                        evidence_id,
                    )
                    continue
                if swap:
                    source_label, source_key = second, second_key
                    target_label, target_key = first, first_key
                else:
                    source_label, source_key = first, first_key
                    target_label, target_key = second, second_key

                source_node = _touch_node(
                    nodes_by_key,
                    node_order,
                    snapshot.snapshot_id,
                    source_label,
                    source_key,
                    evidence_id,
                )
                target_node = _touch_node(
                    nodes_by_key,
                    node_order,
                    snapshot.snapshot_id,
                    target_label,
                    target_key,
                    evidence_id,
                )

                edge_id = (
                    "ged_"
                    + hashlib.sha256(
                        f"{source_node.node_id}|{relation_type}|{target_node.node_id}".encode()
                    ).hexdigest()[:32]
                )
                existing = edges_by_id.get(edge_id)
                if existing is None:
                    edges_by_id[edge_id] = GraphEdge(
                        edge_id,
                        snapshot.snapshot_id,
                        source_node.node_id,
                        target_node.node_id,
                        relation_type,
                        RelationOrigin.EXTRACTED,
                        1.0,
                        (evidence_id,),
                        relation_label,
                    )
                    edge_order.append(edge_id)
                elif evidence_id not in existing.evidence_ids:
                    edges_by_id[edge_id] = replace(
                        existing, evidence_ids=existing.evidence_ids + (evidence_id,)
                    )

    if not edge_order:
        document_node_id = "grn_" + hashlib.sha256(snapshot.snapshot_id.encode()).hexdigest()[:32]
        fallback_node = GraphNode(
            document_node_id,
            snapshot.snapshot_id,
            filename,
            "ENTITY",
            f"document:{snapshot.document_revision_ids[0]}",
            tuple(item.evidence_id for item in evidence),
        )
        return GraphSnapshotDraft(
            replace(snapshot, status="CANDIDATE"), (fallback_node,), (), tuple(evidence), ()
        )

    kept_keys = node_order[:_NODE_CAP]
    nodes = tuple(nodes_by_key[key] for key in kept_keys)
    kept_node_ids = {node.node_id for node in nodes}
    edges = tuple(
        edges_by_id[edge_id]
        for edge_id in edge_order
        if edges_by_id[edge_id].source_node_id in kept_node_ids
        and edges_by_id[edge_id].target_node_id in kept_node_ids
    )
    return GraphSnapshotDraft(
        replace(snapshot, status="CANDIDATE"), nodes, edges, tuple(evidence), ()
    )


class DeterministicGraphExtraction:
    """Extract grounded facts from trigger-word rules without a model."""

    async def extract(self, request: GraphExtractionRequest) -> GraphSnapshotDraft:
        filename = request.document_title or request.snapshot.document_revision_ids[0]
        return rule_based_draft(request.snapshot, request.chunks, filename=filename)
