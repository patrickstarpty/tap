from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tap.interfaces.http.knowledge_review_service import KnowledgeReviewHttpService
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.knowledge.application.review import (
    InMemoryKnowledgeReviewRepository,
    KnowledgeReviewApplication,
    ReviewComparisonTarget,
)
from tap.modules.knowledge.domain.documents import (
    BlockKind,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
)
from tap.modules.knowledge.domain.review import KnowledgeReviewRevision, ReviewStatus
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.modules.knowledge.ports.errors import ArtifactUnavailable

NOW = datetime(2026, 9, 25, 9, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def review(**changes: object) -> KnowledgeReviewRevision:
    return replace(
        KnowledgeReviewRevision(
            review_id="krv_001",
            project_id=VALIDATION_SCOPE.project_id,
            source_revision_ids=("rev_001",),
            inventory_digest=DIGEST,
            chunk_manifest_digest=DIGEST,
            annotation_digest=DIGEST,
            dependency_digest=DIGEST,
            editor_actor_ids=("synthetic-editor-01",),
            reviewer_actor_id=None,
            expires_at=NOW + timedelta(days=30),
            status=ReviewStatus.CHECKING,
            version=3,
            blocking_item_ids=(),
            approved_item_ids=("pi_001",),
        ),
        **changes,
    )


class Projection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        return generation == "generation-001"

    async def generation_for(self, revision):  # type: ignore[no-untyped-def]
        return "generation-001"


class Policy:
    def __init__(self, allowed: frozenset[str]) -> None:
        self.allowed = allowed

    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        return AuthorizationDecision(action in self.allowed, "policy-decision")


class ComparisonRepository(InMemoryKnowledgeReviewRepository):
    def __init__(self, target: ReviewComparisonTarget) -> None:
        super().__init__()
        self.target = target

    async def comparison_target(self, review_id: str, item_id: str):
        return self.target if (review_id, item_id) == ("krv_001", "pi_001") else None


class Artifacts:
    def __init__(self, *, original: bytes | Exception, normalized: NormalizedArtifact | Exception):
        self.original = original
        self.normalized = normalized

    async def read_original(self, locator):  # type: ignore[no-untyped-def]
        if isinstance(self.original, Exception):
            raise self.original
        return self.original

    async def read_normalized(self, locator):  # type: ignore[no-untyped-def]
        if isinstance(self.normalized, Exception):
            raise self.normalized
        return self.normalized


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_allowed_actions_are_intersection_of_state_and_server_policy():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        await repository.add(review())
        service = KnowledgeReviewHttpService(
            KnowledgeReviewApplication(repository, Projection()),
            scope=VALIDATION_SCOPE,
            authorization_policy=Policy(
                frozenset({"knowledge.review.edit", "knowledge.original.read"})
            ),
            clock=lambda: NOW,
        )

        detail = await service.get_review("krv_001")
        assert [item.value for item in detail.allowed_actions] == [
            "edit",
            "submit",
            "read_original",
        ]

        await repository.add(
            review(
                status=ReviewStatus.REVIEWING,
                editor_actor_ids=(VALIDATION_SCOPE.actor_id,),
            )
        )
        reviewing = await service.get_review("krv_001")
        assert "approve" not in {item.value for item in reviewing.allowed_actions}

    run(scenario())


def test_review_comparison_bounds_text_and_never_returns_object_locators():
    async def scenario() -> None:
        target = ReviewComparisonTarget(
            media_type="text/markdown",
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=ArtifactLocator("private/normalized"),
            item_id="pi_001",
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        text = "A" * 5_000
        artifact = NormalizedArtifact(
            filename="rule.md",
            media_type=MediaType.MARKDOWN,
            source_hash=DIGEST,
            blocks=(
                NormalizedBlock(
                    block_id="block-1",
                    kind=BlockKind.PARAGRAPH,
                    text=text,
                    heading_path=(),
                    page=None,
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=len(text),
                    inventory_item_id="pi_001",
                ),
            ),
        )
        application = KnowledgeReviewApplication(
            repository,
            Projection(),
            Artifacts(original=text.encode(), normalized=artifact),  # type: ignore[arg-type]
        )

        comparison = await application.compare_review_item("krv_001", "pi_001")
        assert comparison.original.availability == "available"
        assert comparison.extracted.availability == "available"
        assert len(comparison.original.excerpt or "") == 4_000
        assert len(comparison.extracted.excerpt or "") == 4_000
        assert "private/original" not in repr(comparison)
        assert "private/normalized" not in repr(comparison)

    run(scenario())


def test_review_comparison_reports_artifact_unavailable_instead_of_leaking_provider_failure():
    async def scenario() -> None:
        target = ReviewComparisonTarget(
            media_type="text/plain",
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=ArtifactLocator("private/normalized"),
            item_id="pi_001",
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        application = KnowledgeReviewApplication(
            repository,
            Projection(),
            Artifacts(  # type: ignore[arg-type]
                original=ArtifactUnavailable("provider detail"),
                normalized=ArtifactUnavailable("provider detail"),
            ),
        )

        comparison = await application.compare_review_item("krv_001", "pi_001")
        assert comparison.original.availability == "unavailable"
        assert comparison.original.reason == "original-preview-unavailable"
        assert comparison.extracted.availability == "unavailable"
        assert comparison.extracted.reason == "extraction-preview-unavailable"

    run(scenario())
