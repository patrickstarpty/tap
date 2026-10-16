from __future__ import annotations

import hashlib
import math
import random
import time
from typing import Sequence

from tap.modules.graph.application.alignment import (
    _ambiguous_alias_keys,
    _merge_type_bucket,
    alignment_key,
    merge_edges,
    resolve_entities,
)
from tap.modules.graph.domain.models import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphSnapshotDraft,
    RelationOrigin,
)
from tap.modules.graph.domain.naming import clean_alias, is_bare_grade, is_reference_name


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _snapshot(snapshot_id: str) -> GraphSnapshot:
    return GraphSnapshot.create(
        snapshot_id=snapshot_id,
        project_id="project-1",
        source_revision_ids=(f"{snapshot_id}-source",),
        document_revision_ids=(f"{snapshot_id}-document",),
        status="READY",
    )


def _fragment(
    snapshot_id: str,
    node_specs: Sequence[tuple[str, str, str, str] | tuple[str, str, str, str, tuple[str, ...]]],
) -> GraphSnapshotDraft:
    nodes = []
    for spec in node_specs:
        node_id, label, node_type, canonical_key, *rest = spec
        aliases = rest[0] if rest else ()
        nodes.append(GraphNode(node_id, snapshot_id, label, node_type, canonical_key, (), aliases))
    return GraphSnapshotDraft(_snapshot(snapshot_id), tuple(nodes), (), (), ())


def test_alignment_key_strips_one_trailing_modifier():
    assert alignment_key("核保流程") == "核保" and alignment_key("Refund Policy") == "refund"
    assert alignment_key("流程") == "流程"


def test_exact_key_merges_across_fragments_and_keeps_majority_label():
    a = _fragment(
        "frag-a",
        [
            ("a1", "健康告知", "CONCEPT", "健康告知"),
            ("a2", "健康告知", "CONCEPT", "健康告知"),
        ],
    )
    b = _fragment("frag-b", [("b1", "健康告知书", "CONCEPT", "健康告知")])

    entities = resolve_entities([a, b])

    assert len(entities) == 1
    entity = entities[0]
    assert entity.label == "健康告知"
    assert "健康告知书" in entity.aliases
    assert entity.rule == "EXACT"
    assert len(entity.members) == 3


def _evidenced_node(
    node_id: str,
    snapshot_id: str,
    label: str,
    node_type: str,
    canonical_key: str,
    evidence_count: int,
) -> tuple[GraphNode, tuple[Evidence, ...]]:
    evidence = tuple(
        Evidence(
            evidence_id=f"ev-{node_id}-{i}",
            snapshot_id=snapshot_id,
            source_revision_id=f"{snapshot_id}-source",
            document_revision_id=f"{snapshot_id}-document",
            chunk_id=f"chunk-{node_id}-{i}",
            anchor={"kind": "text", "start": 0, "end": 4},
            content_digest=_digest(f"ev-{node_id}-{i}"),
        )
        for i in range(evidence_count)
    )
    node = GraphNode(
        node_id,
        snapshot_id,
        label,
        node_type,
        canonical_key,
        tuple(e.evidence_id for e in evidence),
    )
    return node, evidence


def test_actor_system_and_entity_merge_on_the_same_canonical_key():
    # Real-model run: the same insurer, same canonical key, split into a
    # "保险公司" ACTOR node (degree 144) and a "保险公司" SYSTEM node across batches.
    actor_node, actor_evidence = _evidenced_node("n1", "frag-a", "保险公司", "ACTOR", "保险公司", 1)
    system_node, system_evidence = _evidenced_node(
        "n2", "frag-a", "保险公司", "SYSTEM", "保险公司", 3
    )
    fragment = GraphSnapshotDraft(
        _snapshot("frag-a"),
        (actor_node, system_node),
        (),
        actor_evidence + system_evidence,
        (),
    )

    entities = resolve_entities([fragment])

    assert len(entities) == 1
    entity = entities[0]
    assert entity.rule == "EXACT"
    assert len(entity.members) == 2
    # The SYSTEM copy carries the most evidence, so the merge publishes SYSTEM.
    assert entity.node_type == "SYSTEM"


def test_a_concept_and_a_requirement_still_require_the_same_type_to_merge():
    # ORG_PARTICIPANT_TYPES only covers ACTOR/SYSTEM/ENTITY; every other type
    # still requires a literal match -- unchanged from before this feature.
    a = _fragment("frag-a", [("n1", "免责条款", "CONCEPT", "免责条款")])
    b = _fragment("frag-b", [("n1", "免责条款", "REQUIREMENT", "免责条款")])
    assert len(resolve_entities([a, b])) == 2


def test_a_company_full_name_and_short_name_merge_by_org_suffix():
    full_name = _fragment(
        "frag-a", [("n1", "友邦人寿保险有限公司", "ACTOR", "友邦人寿保险有限公司")]
    )
    short_name = _fragment("frag-b", [("n1", "友邦人寿", "ACTOR", "友邦人寿")])

    entities = resolve_entities([full_name, short_name])

    assert len(entities) == 1
    assert entities[0].rule == "EXACT"
    assert len(entities[0].members) == 2


def test_a_generic_role_noun_does_not_merge_with_a_specific_company_name():
    # "保险公司" is a role noun, not a specific company -- it must not merge with
    # a real insurer's name just because both are ACTOR-typed.
    generic = _fragment("frag-a", [("n1", "保险公司", "ACTOR", "保险公司")])
    specific = _fragment("frag-b", [("n1", "友邦人寿", "ACTOR", "友邦人寿")])

    assert len(resolve_entities([generic, specific])) == 2


def test_a_fully_generic_company_name_is_not_stripped_to_an_industry_word():
    # "人寿保险有限公司" ("Life Insurance Co., Ltd.") has no distinguishing brand;
    # stripping its suffix would otherwise reduce it to "人寿" or "保险", either
    # of which many unrelated insurers' names also contain, merging them all.
    generic_company = _fragment("frag-a", [("n1", "人寿保险有限公司", "ACTOR", "人寿保险有限公司")])
    another_insurer = _fragment(
        "frag-b", [("n1", "太平人寿保险有限公司", "ACTOR", "太平人寿保险有限公司")]
    )
    bare_generic_word = _fragment("frag-c", [("n1", "人寿", "ACTOR", "人寿")])

    entities = resolve_entities([generic_company, another_insurer, bare_generic_word])

    assert len(entities) == 3


def test_a_corporate_filler_leftover_is_not_treated_as_distinctive_either():
    # "保险股份有限公司" ("Insurance Co., Ltd.") has no distinguishing brand either;
    # the shortest suffix match ("有限公司" alone) would otherwise leave "保险股份"
    # -- not itself in the generic-word list, but exactly as generic once "股份"
    # (corporate-structure filler) is recognized as part of what the word means.
    generic_company = _fragment("frag-a", [("n1", "保险股份有限公司", "ACTOR", "保险股份有限公司")])
    bare_generic_word = _fragment("frag-b", [("n1", "保险", "ACTOR", "保险")])

    entities = resolve_entities([generic_company, bare_generic_word])

    assert len(entities) == 2


def test_same_key_different_type_stays_separate():
    a = _fragment("frag-a", [("n1", "保单", "ENTITY", "保单")])
    b = _fragment("frag-b", [("n1", "保单", "REQUIREMENT", "保单")])
    entities = resolve_entities([a, b])
    assert len(entities) == 2 and {e.node_type for e in entities} == {"ENTITY", "REQUIREMENT"}


def test_alias_level_requires_same_type_and_records_rule():
    same_type = _fragment(
        "frag-a",
        [
            ("a1", "Underwriting", "CONCEPT", "underwriting", ("核保",)),
            ("a2", "核保", "CONCEPT", "核保"),
        ],
    )

    entities = resolve_entities([same_type])
    assert len(entities) == 1
    assert entities[0].rule == "ALIAS"

    other_type = _fragment(
        "frag-a",
        [
            ("a1", "Underwriting", "CONCEPT", "underwriting", ("核保",)),
            ("a2", "核保", "PROCESS", "核保"),
        ],
    )
    separate_entities = resolve_entities([other_type])
    assert len(separate_entities) == 2


def test_a_model_alias_never_merges_entities_of_different_documents():
    # Two products each aliased "重疾险" by their own document: an alias hit alone
    # does not cross documents (different source revisions).
    a = _fragment("frag-a", [("a1", "安心重疾保险", "SYSTEM", "安心重疾保险", ("重疾险",))])
    b = _fragment("frag-b", [("b1", "康健重疾保险", "SYSTEM", "康健重疾保险", ("重疾险",))])
    assert len(resolve_entities([a, b])) == 2

    # Nor does one document's alias matching the other document's name.
    b_named = _fragment("frag-b", [("b1", "重疾险", "SYSTEM", "重疾险")])
    assert len(resolve_entities([a, b_named])) == 2


def test_the_same_name_still_merges_across_documents_by_alias_level():
    a = _fragment("frag-a", [("a1", "核保", "CONCEPT", "核保流程审核")])
    b = _fragment("frag-b", [("b1", "核保", "CONCEPT", "核保")])

    entities = resolve_entities([a, b])

    assert len(entities) == 1 and entities[0].rule == "ALIAS"


def test_an_alias_still_merges_within_one_document_and_exact_keys_across():
    a = _fragment(
        "frag-a",
        [
            ("a1", "安心重疾保险", "SYSTEM", "安心重疾保险", ("重疾险",)),
            ("a2", "重疾险", "SYSTEM", "重疾险"),
        ],
    )
    b = _fragment("frag-b", [("b1", "重疾险", "SYSTEM", "安心重疾保险")])

    entities = resolve_entities([a, b])

    assert len(entities) == 1
    assert {member for member in entities[0].members} == {
        ("frag-a", "a1"),
        ("frag-a", "a2"),
        ("frag-b", "b1"),
    }


def test_embedding_level_only_when_vectors_supplied():
    a = _fragment("frag-a", [("a1", "X", "ENTITY", "keyx")])
    b = _fragment("frag-b", [("b1", "Y", "ENTITY", "keyy")])

    assert len(resolve_entities([a, b])) == 2

    embeddings = {
        ("frag-a", "a1"): (1.0, 0.0),
        ("frag-b", "b1"): (1.0, 0.0),
    }
    merged = resolve_entities([a, b], embeddings=embeddings, threshold=0.92)
    assert len(merged) == 1
    assert merged[0].rule == "EMBEDDING"


def _edge_fragment(
    snapshot_id: str,
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, str, str, float, str, str]],
) -> GraphSnapshotDraft:
    snapshot = _snapshot(snapshot_id)
    graph_nodes = tuple(
        GraphNode(node_id, snapshot_id, node_id, "ENTITY", node_id) for node_id in nodes
    )
    evidence = []
    graph_edges = []
    for edge_id, source, target, relation_type, confidence, label, evidence_id in edges:
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                snapshot_id=snapshot_id,
                source_revision_id=f"{snapshot_id}-source",
                document_revision_id=f"{snapshot_id}-document",
                chunk_id=f"chunk-{evidence_id}",
                anchor={"kind": "text", "start": 0, "end": 4},
                content_digest=_digest(evidence_id),
            )
        )
        graph_edges.append(
            GraphEdge(
                edge_id=edge_id,
                snapshot_id=snapshot_id,
                source_node_id=source,
                target_node_id=target,
                relation_type=relation_type,
                origin=RelationOrigin.EXTRACTED,
                confidence=confidence,
                evidence_ids=(evidence_id,),
                relation_label=label,
            )
        )
    return GraphSnapshotDraft(snapshot, graph_nodes, tuple(graph_edges), tuple(evidence), ())


def test_merge_edges_unions_evidence_and_takes_max_confidence():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "需要", "ev-a1")],
    )
    b = _edge_fragment(
        "frag-b",
        ["m-src", "m-dst", "m-other"],
        [
            ("e2", "m-src", "m-dst", "REQUIRES", 0.9, "需要", "ev-b1"),
            ("e3", "m-src", "m-other", "REQUIRES", 0.5, "须提供", "ev-b2"),
        ],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
        ("frag-b", "m-src"): "pid-src",
        ("frag-b", "m-dst"): "pid-dst",
        ("frag-b", "m-other"): "pid-src",
    }

    merged = merge_edges([a, b], entity_of)

    assert len(merged) == 1
    edge, evidence_pairs = merged[0]
    assert edge.source_node_id == "pid-src"
    assert edge.target_node_id == "pid-dst"
    assert edge.confidence == 0.9
    assert edge.relation_label == "需要"
    assert len(evidence_pairs) == 2


def test_merge_edges_ignores_blank_label_when_a_real_label_exists():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "", "ev-a1")],
    )
    b = _edge_fragment(
        "frag-b",
        ["m-src", "m-dst"],
        [("e2", "m-src", "m-dst", "REQUIRES", 0.9, "需要", "ev-b1")],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
        ("frag-b", "m-src"): "pid-src",
        ("frag-b", "m-dst"): "pid-dst",
    }

    merged = merge_edges([a, b], entity_of)

    assert len(merged) == 1
    edge, _ = merged[0]
    assert edge.relation_label == "需要"


def test_merge_edges_falls_back_to_relation_type_when_all_labels_blank():
    a = _edge_fragment(
        "frag-a",
        ["n-src", "n-dst"],
        [("e1", "n-src", "n-dst", "REQUIRES", 0.6, "", "ev-a1")],
    )
    entity_of = {
        ("frag-a", "n-src"): "pid-src",
        ("frag-a", "n-dst"): "pid-dst",
    }

    merged = merge_edges([a], entity_of)

    assert len(merged) == 1
    edge, _ = merged[0]
    assert edge.relation_label == "REQUIRES"
    assert edge.relation_label != ""


def _linear_scan_clusters(
    fragments: Sequence[GraphSnapshotDraft],
    embeddings: dict[tuple[str, str], tuple[float, ...]] | None,
    threshold: float,
) -> list[tuple[str, tuple[tuple[str, str], ...], str]]:
    """Reference resolver that scans every cluster for every node, in creation order.

    It states the alignment semantics directly (first cluster of the same type whose
    level-1 keys contain the node's key, else first holding the node's name or, from
    the node's own document, any of its level-2 keys, else the best embedding match)
    so the indexed resolver can be checked against it.
    """
    ordered = sorted(fragments, key=lambda item: item.snapshot.snapshot_id)
    ambiguous = _ambiguous_alias_keys(ordered)

    # Independent restatement of "a (bucket, key1) names at most one cluster,
    # system-wide, whoever used it first": precompute, in a separate pass
    # before any cluster exists, which node (by identity) is the first to use
    # each key1 as its EXACT key. A later node with the same key1 then simply
    # looks up *that node's* cluster once it exists -- rather than mutating a
    # key-to-cluster dict as clusters are built (which is what the indexed
    # resolver itself does), this is a plain precomputed fact about the input,
    # checked against a node-to-cluster map filled in as nodes are placed. A
    # bare grade (naming.is_bare_grade) may not establish or claim ownership:
    # it is kept as a node's own name, but never merges with another node of
    # the same literal canonical-key text (see alignment.py's own EXACT guard).
    first_exact_owner: dict[tuple[str, str], tuple[str, str]] = {}
    for fragment in ordered:
        for node in fragment.nodes:
            key1 = alignment_key(node.canonical_key, node.node_type)
            if is_bare_grade(key1):
                continue
            bucket = _merge_type_bucket(node.node_type)
            first_exact_owner.setdefault(
                (bucket, key1), (fragment.snapshot.snapshot_id, node.node_id)
            )

    def names(texts, node_type):
        keys = set()
        for text in texts:
            cleaned = clean_alias(text)
            if cleaned is not None:
                keys.add(alignment_key(cleaned, node_type))
        return keys

    clusters: list[dict[str, object]] = []
    node_cluster: dict[tuple[str, str], dict[str, object]] = {}

    for fragment in ordered:
        snapshot_id = fragment.snapshot.snapshot_id
        source = fragment.snapshot.source_revision_ids
        for node in fragment.nodes:
            node_identity = (snapshot_id, node.node_id)
            key1 = alignment_key(node.canonical_key, node.node_type)
            label_keys = names((node.label,), node.node_type)
            keys2 = {
                key
                for key in names((node.label, *node.aliases), node.node_type)
                if (node.node_type, source, key) not in ambiguous
            }
            target = None
            rule = None

            if not is_bare_grade(key1):
                bucket = _merge_type_bucket(node.node_type)
                owner = first_exact_owner.get((bucket, key1))
                if owner is not None and owner != node_identity:
                    target, rule = node_cluster[owner], "EXACT"

            if target is None:
                # Names match across documents; aliases only within one
                # document. Scans every existing cluster (independent of the
                # indexed resolver's own label_index/alias_index dicts), but
                # each cluster tracks its label/alias keys *per contributing
                # node type* (not pooled into one cluster-wide set): once
                # EXACT can place members of different literal types in one
                # cluster (ORG_PARTICIPANT_TYPES), a key contributed by one
                # member's type must not satisfy a lookup under another
                # member's type.
                for cluster in clusters:
                    cluster_labels = cluster["labels_by_type"].get(node.node_type, set())
                    cluster_aliases = cluster["aliases_by_type_source"].get(
                        (node.node_type, source), set()
                    )
                    if cluster_labels & label_keys or cluster_aliases & keys2:
                        target, rule = cluster, "ALIAS"
                        break

            if target is None and embeddings:
                vector = embeddings.get(node_identity)
                if vector is not None:
                    best, best_score = None, -1.0
                    for cluster in clusters:
                        # Matches a same-type *member*, not a same-type cluster: a
                        # member joining later via the ORG_PARTICIPANT_TYPES EXACT
                        # bucket can be a different literal type than the one
                        # that created the cluster, and still be the right
                        # embedding match for a query of that member's own type.
                        for member, member_type in zip(cluster["members"], cluster["member_types"]):
                            if member_type != node.node_type:
                                continue
                            other = embeddings.get(member)
                            if other is None:
                                continue
                            dot = sum(x * y for x, y in zip(vector, other))
                            norm_vector = math.sqrt(sum(x * x for x in vector))
                            norm_other = math.sqrt(sum(y * y for y in other))
                            norm_product = norm_vector * norm_other
                            score = 0.0 if norm_product == 0.0 else dot / norm_product
                            if score >= threshold and score > best_score:
                                best, best_score = cluster, score
                    if best is not None:
                        target, rule = best, "EMBEDDING"
            if target is None:
                target = {
                    "type": node.node_type,
                    "members": [],
                    "member_types": [],
                    "labels_by_type": {},
                    "aliases_by_type_source": {},
                    "rule": "EXACT",
                }
                clusters.append(target)
                rule = "EXACT"
            node_cluster[node_identity] = target
            target["members"].append(node_identity)
            target["member_types"].append(node.node_type)
            target["labels_by_type"].setdefault(node.node_type, set()).update(label_keys)
            target["aliases_by_type_source"].setdefault((node.node_type, source), set()).update(
                keys2
            )
            target["rule"] = rule
    return [
        (str(cluster["type"]), tuple(cluster["members"]), str(cluster["rule"]))
        for cluster in clusters
    ]


def test_indexed_alignment_matches_a_full_cluster_scan_on_overlapping_fragments():
    import random

    words = [
        "核保",
        "理赔",
        "保单",
        "退保",
        "refund",
        "claim",
        "policy holder",
        "agent",
        # Org/participant-shaped words: exercise the ACTOR/SYSTEM/ENTITY EXACT
        # type bucket and the corporate-suffix normalization together.
        "友邦人寿",
        "太平人寿",
        "人寿保险",
        # Clause-prefixed terms: exercise clean_alias's leading-article strip
        # (and, combined with a corporate suffix below, both affixes at once).
        "第十九条核保",
        "第一部分理赔",
        # Bare classification grades: exercise is_bare_grade's EXACT-matching
        # guard (combined with a non-empty suffix below, a *qualified* grade,
        # which is not a bare grade and merges normally).
        "Ⅳ级",
        "三级",
    ]
    suffixes = ["", "", "规则", "流程", " policy", " rule", "有限公司", "保险有限公司"]
    node_types = ["CONCEPT", "ENTITY", "PROCESS", "ACTOR", "SYSTEM"]
    directions = [(1.0, 0.0), (0.0, 1.0), (0.96, 0.28), (0.7, 0.7)]

    for seed in range(40):
        rng = random.Random(seed)
        fragments = []
        embeddings: dict[tuple[str, str], tuple[float, ...]] = {}
        for fragment_index in range(rng.randint(2, 6)):
            snapshot_id = f"frag-{rng.randint(0, 99):02d}-{fragment_index}"
            specs = []
            for node_index in range(rng.randint(1, 12)):
                key = rng.choice(words) + rng.choice(suffixes)
                label = rng.choice(words) + rng.choice(suffixes)
                aliases = tuple(
                    dict.fromkeys(
                        rng.choice(words) + rng.choice(suffixes) for _ in range(rng.randint(0, 2))
                    )
                )
                node_id = f"n{node_index}"
                specs.append((node_id, label, rng.choice(node_types), key, aliases))
                if rng.random() < 0.5:
                    embeddings[(snapshot_id, node_id)] = rng.choice(directions)
            fragments.append(_fragment(snapshot_id, specs))

        for supplied in (None, embeddings):
            expected = _linear_scan_clusters(fragments, supplied, 0.92)
            actual = [
                (entity.node_type, entity.members, entity.rule)
                for entity in resolve_entities(fragments, embeddings=supplied, threshold=0.92)
            ]
            assert actual == expected, f"seed {seed} embeddings={supplied is not None}"


def _embedding_fragments(
    rng: random.Random,
    *,
    node_count: int,
    node_types: Sequence[str],
    vector_for,
    key_space: int,
    vector_rate: float = 0.85,
) -> tuple[list[GraphSnapshotDraft], dict[tuple[str, str], tuple[float, ...]]]:
    """Nodes with keys drawn from ``key_space`` names (so EXACT/ALIAS still
    build multi-member clusters whose members carry different vectors) spread
    over several fragments; most nodes carry a vector from ``vector_for``."""
    fragments: list[GraphSnapshotDraft] = []
    embeddings: dict[tuple[str, str], tuple[float, ...]] = {}
    remaining = node_count
    fragment_index = 0
    while remaining > 0:
        size = min(remaining, rng.randint(1, 30))
        remaining -= size
        snapshot_id = f"frag-{rng.randint(0, 999):03d}-{fragment_index}"
        fragment_index += 1
        specs = []
        for node_index in range(size):
            node_id = f"n{node_index}"
            key = f"key{rng.randrange(key_space)}"
            label = f"label{rng.randrange(key_space)}"
            specs.append((node_id, label, rng.choice(node_types), key))
            if rng.random() < vector_rate:
                embeddings[(snapshot_id, node_id)] = vector_for(rng)
        fragments.append(_fragment(snapshot_id, specs))
    return fragments, embeddings


def _resolved_clusters(
    fragments: Sequence[GraphSnapshotDraft],
    embeddings: dict[tuple[str, str], tuple[float, ...]],
    threshold: float,
) -> list[tuple[str, tuple[tuple[str, str], ...], str]]:
    return [
        (entity.node_type, entity.members, entity.rule)
        for entity in resolve_entities(fragments, embeddings=embeddings, threshold=threshold)
    ]


def test_embedding_alignment_matches_a_full_scan_on_clustered_random_vectors():
    for seed in range(12):
        rng = random.Random(1000 + seed)
        dim = rng.choice((8, 16, 64))
        centers = [tuple(rng.gauss(0.0, 1.0) for _ in range(dim)) for _ in range(12)]

        def clustered(local: random.Random) -> tuple[float, ...]:
            center = local.choice(centers)
            return tuple(value + local.gauss(0.0, 0.2) for value in center)

        fragments, embeddings = _embedding_fragments(
            rng,
            node_count=rng.randint(80, 240),
            node_types=("CONCEPT", "ENTITY", "PROCESS")[: rng.randint(1, 3)],
            vector_for=clustered,
            key_space=400,
        )
        for threshold in (0.92, 0.8):
            expected = _linear_scan_clusters(fragments, embeddings, threshold)
            actual = _resolved_clusters(fragments, embeddings, threshold)
            assert actual == expected, f"seed {seed} threshold {threshold}"
            assert any(rule == "EMBEDDING" for _, _, rule in actual), f"seed {seed}"


def test_embedding_alignment_matches_a_full_scan_on_ties_and_boundary_scores():
    """Lattice directions, scaled duplicates and zero vectors produce exact
    score ties across clusters and scores landing exactly on the threshold;
    the earliest-created cluster must still win every tie."""
    lattice = [
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 0.0),
        (0.0, 0.0, 0.0),
    ]

    def lattice_vector(local: random.Random) -> tuple[float, ...]:
        scale = local.choice((1.0, 1.0, 2.0, 0.5, 3.0, 1e-3))
        return tuple(scale * value for value in local.choice(lattice))

    boundary = 1.0 / math.sqrt(2.0)
    for seed in range(30):
        rng = random.Random(2000 + seed)
        fragments, embeddings = _embedding_fragments(
            rng,
            node_count=rng.randint(10, 80),
            node_types=("CONCEPT", "ENTITY")[: rng.randint(1, 2)],
            vector_for=lattice_vector,
            key_space=rng.choice((6, 40)),
        )
        for threshold in (0.92, boundary, 0.5, 0.0):
            expected = _linear_scan_clusters(fragments, embeddings, threshold)
            actual = _resolved_clusters(fragments, embeddings, threshold)
            assert actual == expected, f"seed {seed} threshold {threshold}"


def test_embedding_alignment_matches_a_full_scan_on_mixed_dimensions_and_extreme_scales():
    """Vectors the index cannot hold faithfully (mixed lengths within one type,
    norms far outside a normal float range) still align exactly as the full
    scan does."""

    def irregular(local: random.Random) -> tuple[float, ...]:
        dim = local.choice((3, 3, 3, 4))
        scale = local.choice((1.0, 1.0, 1e200, 1e-200))
        return tuple(scale * local.choice((-1.0, 0.0, 1.0, 0.5)) for _ in range(dim))

    for seed in range(20):
        rng = random.Random(3000 + seed)
        fragments, embeddings = _embedding_fragments(
            rng,
            node_count=rng.randint(10, 60),
            node_types=("CONCEPT", "ENTITY"),
            vector_for=irregular,
            key_space=50,
        )
        for threshold in (0.92, 0.5):
            expected = _linear_scan_clusters(fragments, embeddings, threshold)
            actual = _resolved_clusters(fragments, embeddings, threshold)
            assert actual == expected, f"seed {seed} threshold {threshold}"


def test_embedding_alignment_of_two_thousand_single_type_nodes_stays_fast():
    rng = random.Random(7)
    dim = 64
    centers = [tuple(rng.gauss(0.0, 1.0) for _ in range(dim)) for _ in range(500)]

    def clustered(local: random.Random) -> tuple[float, ...]:
        center = local.choice(centers)
        return tuple(value + local.gauss(0.0, 0.15) for value in center)

    fragments, embeddings = _embedding_fragments(
        rng,
        node_count=2000,
        node_types=("CONCEPT",),
        vector_for=clustered,
        key_space=10**9,
        vector_rate=1.0,
    )

    started = time.perf_counter()
    entities = resolve_entities(fragments, embeddings=embeddings, threshold=0.92)
    elapsed = time.perf_counter() - started

    assert any(entity.rule == "EMBEDDING" for entity in entities)
    # The full scan takes well over ten seconds here; the indexed layer takes
    # a fraction of a second, so this budget leaves wide headroom for CI.
    assert elapsed < 3.0, elapsed


def _real_alias_fragments() -> list[GraphSnapshotDraft]:
    """The fragment nodes behind the 9 ALIAS merges of the real-model run."""
    import json
    from pathlib import Path

    samples = json.loads(
        (Path(__file__).parents[2] / "fixtures/graph/real_extraction_replay.json").read_text()
    )["aliasMerges"]
    specs: dict[str, dict[str, tuple[str, str, str, str, tuple[str, ...]]]] = {}
    for sample in samples:
        for member in sample["members"]:
            specs.setdefault(member["snapshot"], {})[member["id"]] = (
                member["id"],
                member["label"],
                member["type"],
                member["key"],
                tuple(member["aliases"]),
            )
    return [_fragment(snapshot_id, list(nodes.values())) for snapshot_id, nodes in specs.items()]


def _merged_labels(entities) -> set[frozenset[str]]:
    return {
        frozenset(entity.aliases) | {entity.label} for entity in entities if len(entity.members) > 1
    }


def test_reference_aliases_from_the_real_run_no_longer_merge_entities():
    entities = resolve_entities(_real_alias_fragments())

    merged = _merged_labels(entities)
    # Before: "本合同" merged two products, "第十九条"/"第十五条"/"第十六条"/"第十七条"/
    # "第二十条" merged unrelated clauses, "释义四" merged two definitions.
    assert merged == {
        # Same document: the insurer, named "本公司" with "友邦保险" as its alias.
        frozenset({"友邦保险"}),
        frozenset({"境内医院", "医院"}),
        # Two documents, same canonical key: an EXACT merge, not an alias one.
        frozenset({"明确说明与如实告知"}),
    }
    assert not any(is_reference_name(alias) for entity in entities for alias in entity.aliases)


def test_an_alias_shared_by_three_distinct_entities_in_one_document_does_not_merge_them():
    # Real-model run: three distinct cardiomyopathies in the same filing were all
    # glossed "心肌病", and the shared alias merged them into one node.
    fragment = _fragment(
        "frag-a",
        [
            ("n1", "扩张型心肌病", "CONCEPT", "扩张型心肌病", ("心肌病",)),
            ("n2", "肥厚型心肌病", "CONCEPT", "肥厚型心肌病", ("心肌病",)),
            ("n3", "限制型心肌病", "CONCEPT", "限制型心肌病", ("心肌病",)),
        ],
    )

    entities = resolve_entities([fragment])

    assert {entity.label for entity in entities} == {"扩张型心肌病", "肥厚型心肌病", "限制型心肌病"}
    assert all(len(entity.members) == 1 for entity in entities)


def test_an_owned_alias_still_does_not_absorb_two_or_more_distinct_aliasers():
    # A generic "心肌病" node (the key's own owner) does not rescue the shared
    # alias either: three distinct cardiomyopathies all glossing "心肌病" is
    # exactly as ambiguous whether or not something else really is named that.
    fragment = _fragment(
        "frag-a",
        [
            ("n0", "心肌病", "CONCEPT", "心肌病"),
            ("n1", "扩张型心肌病", "CONCEPT", "扩张型心肌病", ("心肌病",)),
            ("n2", "肥厚型心肌病", "CONCEPT", "肥厚型心肌病", ("心肌病",)),
            ("n3", "限制型心肌病", "CONCEPT", "限制型心肌病", ("心肌病",)),
        ],
    )

    entities = resolve_entities([fragment])

    assert {entity.label for entity in entities} == {
        "心肌病",
        "扩张型心肌病",
        "肥厚型心肌病",
        "限制型心肌病",
    }
    assert all(len(entity.members) == 1 for entity in entities)


def test_an_owned_alias_still_merges_its_one_legitimate_aliaser():
    # Exactly one distinct aliaser attaching to the key's owner is the ordinary,
    # wanted case and must keep working regardless of this guard.
    fragment = _fragment(
        "frag-a",
        [
            ("a1", "Underwriting", "CONCEPT", "underwriting", ("核保",)),
            ("a2", "核保", "CONCEPT", "核保"),
        ],
    )

    entities = resolve_entities([fragment])

    assert len(entities) == 1
    assert entities[0].rule == "ALIAS"
    assert len(entities[0].members) == 2


def test_an_alias_shared_by_two_grades_in_one_document_does_not_merge_them():
    # Real-model run: an NYHA heart-function grade and an unrelated rheumatoid-
    # arthritis functional grade were both glossed "IV 级"/"Ⅳ级" in the same filing.
    fragment = _fragment(
        "frag-a",
        [
            ("n1", "心功能分级Ⅳ级", "CONCEPT", "心功能分级Ⅳ级", ("Ⅳ级",)),
            (
                "n2",
                "类风湿性关节炎功能分类IV级",
                "CONCEPT",
                "类风湿性关节炎功能分类IV级",
                ("IV 级",),
            ),
        ],
    )

    entities = resolve_entities([fragment])

    assert {entity.label for entity in entities} == {
        "心功能分级Ⅳ级",
        "类风湿性关节炎功能分类IV级",
    }
    assert all(len(entity.members) == 1 for entity in entities)


def test_a_bare_grade_canonical_key_never_exact_merges_across_documents():
    # A bare grade is kept as a node's own name (naming.is_bare_grade), but an
    # EXACT match compares canonical_key text literally and does not go through
    # naming's alias filtering -- without a dedicated guard, an NYHA "Ⅳ级" node
    # and an unrelated RA "Ⅳ级" node, same literal canonical_key text across two
    # documents, would EXACT-merge into one.
    a = _fragment("frag-a", [("n1", "Ⅳ级", "CONCEPT", "Ⅳ级")])
    b = _fragment("frag-b", [("n1", "Ⅳ级", "CONCEPT", "Ⅳ级")])

    entities = resolve_entities([a, b])

    assert len(entities) == 2
    assert all(len(entity.members) == 1 for entity in entities)


def test_a_bare_grade_canonical_key_never_exact_merges_within_one_document():
    fragment = _fragment(
        "frag-a",
        [
            ("n1", "Ⅳ级", "CONCEPT", "Ⅳ级"),
            ("n2", "Ⅳ级", "CONCEPT", "Ⅳ级"),
        ],
    )

    entities = resolve_entities([fragment])

    assert len(entities) == 2
    assert all(len(entity.members) == 1 for entity in entities)


def test_an_ambiguous_alias_does_not_block_a_different_entitys_safe_merge():
    # The ambiguity guard only disqualifies the ambiguous key itself; an unrelated
    # pair sharing a different, unambiguous alias in the same document still merges.
    fragment = _fragment(
        "frag-a",
        [
            ("n1", "扩张型心肌病", "CONCEPT", "扩张型心肌病", ("心肌病",)),
            ("n2", "肥厚型心肌病", "CONCEPT", "肥厚型心肌病", ("心肌病",)),
            ("n3", "安心重疾保险", "SYSTEM", "安心重疾保险", ("重疾险",)),
            ("n4", "重疾险", "SYSTEM", "重疾险"),
        ],
    )

    entities = resolve_entities([fragment])

    merged = {entity.label: entity for entity in entities if len(entity.members) > 1}
    assert set(merged) == {"安心重疾保险"}
