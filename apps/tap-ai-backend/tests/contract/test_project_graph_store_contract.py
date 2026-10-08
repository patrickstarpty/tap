from __future__ import annotations

from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import ProjectEdge, ProjectGraphDraft, ProjectNode
from tap.modules.graph.ports.project_store import ProjectGraphNotReady, ProjectGraphVersionMismatch


def _draft(fragment_digest: str = "sha256:" + "a" * 64) -> ProjectGraphDraft:
    return ProjectGraphDraft(
        fragment_digest=fragment_digest,
        nodes=(
            ProjectNode(
                node_id="node-1",
                label="Policy",
                node_type="ENTITY",
                canonical_key="policy",
                degree=1,
            ),
            ProjectNode(
                node_id="node-2", label="Claim", node_type="ENTITY", canonical_key="claim", degree=1
            ),
        ),
        edges=(
            ProjectEdge(
                "edge-1", "node-1", "node-2", "GOVERNS", "需要", RelationOrigin.INFERRED, 1.0
            ),
        ),
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
        merge_log=(),
    )


def _draft_with_node(fragment_digest: str, node_id: str) -> ProjectGraphDraft:
    """A single-node draft, so each published version is trivially
    distinguishable by which node its `overview()` returns."""

    return ProjectGraphDraft(
        fragment_digest=fragment_digest,
        nodes=(
            ProjectNode(
                node_id=node_id, label=node_id, node_type="ENTITY", canonical_key=node_id, degree=0
            ),
        ),
        edges=(),
        node_sources=(),
        edge_evidence=(),
        aliases=(),
        communities=(),
        merge_log=(),
    )


@pytest.mark.asyncio
async def test_store_requires_a_ready_version() -> None:
    store = InMemoryProjectGraphStore()
    assert await store.get_current(VALIDATION_SCOPE) is None
    with pytest.raises(ProjectGraphNotReady):
        await store.overview(VALIDATION_SCOPE)


@pytest.mark.asyncio
async def test_same_digest_republish_returns_existing_version() -> None:
    store = InMemoryProjectGraphStore()
    now = datetime(2026, 10, 7, 9, 0, 0)
    first = await store.publish(VALIDATION_SCOPE, _draft(), now=now)
    second = await store.publish(VALIDATION_SCOPE, _draft(), now=now)
    assert first.version == second.version == 1
    current = await store.get_current(VALIDATION_SCOPE)
    assert current is not None and current.version == 1


@pytest.mark.asyncio
async def test_republish_after_digest_reverts_creates_a_new_version() -> None:
    """A source set changing A -> B -> A must publish v3, not silently return
    the stale v1 (dedup only compares against the *current* version's digest,
    per the controller ruling fixing `_digests`-keyed dedup)."""

    store = InMemoryProjectGraphStore()
    now = datetime(2026, 10, 7, 9, 0, 0)
    draft_a = _draft_with_node("sha256:" + "a" * 64, "node-a")
    draft_b = _draft_with_node("sha256:" + "b" * 64, "node-b")

    v1 = await store.publish(VALIDATION_SCOPE, draft_a, now=now)
    v2 = await store.publish(VALIDATION_SCOPE, draft_b, now=now)
    v3 = await store.publish(VALIDATION_SCOPE, draft_a, now=now)

    assert (v1.version, v2.version, v3.version) == (1, 2, 3)
    current = await store.get_current(VALIDATION_SCOPE)
    assert current is not None and current.version == 3
    overview = await store.overview(VALIDATION_SCOPE)
    assert {node.node_id for node in overview.nodes} == {"node-a"}


@pytest.mark.asyncio
async def test_version_pin_serves_a_retained_older_version() -> None:
    store = InMemoryProjectGraphStore()
    now = datetime(2026, 10, 7, 9, 0, 0)
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "1" * 64, "node-v1"), now=now
    )
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "2" * 64, "node-v2"), now=now
    )
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "3" * 64, "node-v3"), now=now
    )

    pinned = await store.overview(VALIDATION_SCOPE, version=2)
    assert pinned.version == 2
    assert {node.node_id for node in pinned.nodes} == {"node-v2"}

    current = await store.overview(VALIDATION_SCOPE)
    assert current.version == 3
    assert {node.node_id for node in current.nodes} == {"node-v3"}


@pytest.mark.asyncio
async def test_version_pin_outside_retention_window_raises_mismatch_with_current() -> None:
    store = InMemoryProjectGraphStore()
    now = datetime(2026, 10, 7, 9, 0, 0)
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "1" * 64, "node-v1"), now=now
    )
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "2" * 64, "node-v2"), now=now
    )
    await store.publish(
        VALIDATION_SCOPE, _draft_with_node("sha256:" + "3" * 64, "node-v3"), now=now
    )

    with pytest.raises(ProjectGraphVersionMismatch) as excinfo:
        await store.overview(VALIDATION_SCOPE, version=1)
    assert excinfo.value.current == 3


@pytest.mark.asyncio
async def test_cross_project_reads_see_no_graph() -> None:
    store = InMemoryProjectGraphStore()
    now = datetime(2026, 10, 7, 9, 0, 0)
    await store.publish(VALIDATION_SCOPE, _draft(), now=now)
    other = ProjectScopeContext(
        enterprise_id=VALIDATION_SCOPE.enterprise_id,
        project_id="other-project",
        actor_id=VALIDATION_SCOPE.actor_id,
        identity_mode=IdentityMode.VALIDATION,
    )
    assert await store.get_current(other) is None
    with pytest.raises(ProjectGraphNotReady):
        await store.overview(other)
