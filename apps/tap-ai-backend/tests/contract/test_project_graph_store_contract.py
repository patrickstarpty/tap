from __future__ import annotations

from datetime import datetime

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.domain.models import RelationOrigin
from tap.modules.graph.domain.project import ProjectEdge, ProjectGraphDraft, ProjectNode
from tap.modules.graph.ports.project_store import ProjectGraphNotReady


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
