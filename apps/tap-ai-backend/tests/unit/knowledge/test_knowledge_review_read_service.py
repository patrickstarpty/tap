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
    canonical_sha256,
)
from tap.modules.knowledge.domain.parse_inventory import OriginalExcerptRange
from tap.modules.knowledge.domain.review import KnowledgeReviewRevision, ReviewStatus
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure, ArtifactUnavailable

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
    def __init__(
        self,
        allowed: frozenset[str],
        *,
        resource_ids: dict[str, str] | None = None,
    ) -> None:
        self.allowed = allowed
        self.resource_ids = resource_ids or {}
        self.calls: list[tuple[str, str, str | None]] = []

    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        self.calls.append((action, resource.kind, resource.resource_id))
        allowed = action in self.allowed and (
            action not in self.resource_ids or self.resource_ids[action] == resource.resource_id
        )
        return AuthorizationDecision(allowed, "policy-decision")


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
        self.original_read_calls = 0
        self.original_excerpt_reads: list[tuple[object, ...]] = []

    async def read_original(self, locator):  # type: ignore[no-untyped-def]
        self.original_read_calls += 1
        if isinstance(self.original, Exception):
            raise self.original
        return self.original

    async def read_original_excerpt(  # type: ignore[no-untyped-def]
        self,
        locator,
        *,
        revision_id,
        source_digest,
        start_byte,
        end_byte,
        excerpt_digest,
    ):
        self.original_excerpt_reads.append(
            (
                locator,
                revision_id,
                source_digest,
                start_byte,
                end_byte,
                excerpt_digest,
            )
        )
        if isinstance(self.original, Exception):
            raise self.original
        value = self.original[start_byte:end_byte]
        if canonical_sha256(value) != excerpt_digest:
            raise ArtifactIntegrityFailure("changed source excerpt")
        return value

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


def test_allowed_actions_share_command_resource_targets_expiry_and_inventory_preconditions():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, Projection())
        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )
        publication = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-actions",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )
        policy = Policy(
            frozenset({"knowledge.publish", "knowledge.original.read"}),
            resource_ids={
                "knowledge.publish": publication.publication_id,
                "knowledge.original.read": "krv_001",
            },
        )
        service = KnowledgeReviewHttpService(
            application,
            scope=VALIDATION_SCOPE,
            authorization_policy=policy,
            clock=lambda: NOW,
        )

        published = await service.get_review("krv_001")
        assert [item.value for item in published.allowed_actions] == [
            "withdraw",
            "read_original",
        ]
        assert published.publication_ids == [publication.publication_id]
        assert (
            "knowledge.publish",
            "knowledge-publication",
            publication.publication_id,
        ) in policy.calls

        await repository.add(
            review(expires_at=NOW),
            inventory_item_ids=("pi_001",),
        )
        expired_policy = Policy(frozenset({"knowledge.review.edit", "knowledge.original.read"}))
        expired = await KnowledgeReviewHttpService(
            application,
            scope=VALIDATION_SCOPE,
            authorization_policy=expired_policy,
            clock=lambda: NOW,
        ).get_review("krv_001")
        assert {item.value for item in expired.allowed_actions} == {"read_original"}

        await repository.add(review(), inventory_item_ids=("pi_other",))
        stale = await KnowledgeReviewHttpService(
            application,
            scope=VALIDATION_SCOPE,
            authorization_policy=Policy(
                frozenset({"knowledge.review.edit", "knowledge.original.read"})
            ),
            clock=lambda: NOW,
        ).get_review("krv_001")
        assert {item.value for item in stale.allowed_actions} == {"read_original"}

    run(scenario())


def test_review_comparison_uses_the_exact_late_item_without_reading_the_whole_original():
    async def scenario() -> None:
        prefix = "A" * 1_000_000
        target_text = "后部精确条款"
        original = (prefix + target_text).encode()
        start = len(prefix.encode())
        excerpt = target_text.encode()
        target = ReviewComparisonTarget(
            media_type="text/markdown",
            source_revision_id="rev_001",
            source_digest=canonical_sha256(original),
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=ArtifactLocator("private/normalized"),
            item_id="pi_001",
            original_excerpt=OriginalExcerptRange(
                source_digest=canonical_sha256(original),
                start_byte=start,
                end_byte=start + len(excerpt),
                excerpt_digest=canonical_sha256(excerpt),
            ),
            original_alignment_reason=None,
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        artifact = NormalizedArtifact(
            filename="rule.md",
            media_type=MediaType.MARKDOWN,
            source_hash=DIGEST,
            blocks=(
                NormalizedBlock(
                    block_id="block-prefix",
                    kind=BlockKind.PARAGRAPH,
                    text=prefix,
                    heading_path=(),
                    page=None,
                    paragraph_index=0,
                    start_offset=0,
                    end_offset=len(prefix),
                    inventory_item_id="pi_other",
                ),
                NormalizedBlock(
                    block_id="block-target",
                    kind=BlockKind.PARAGRAPH,
                    text=target_text,
                    heading_path=(),
                    page=None,
                    paragraph_index=1,
                    start_offset=len(prefix),
                    end_offset=len(prefix) + len(target_text),
                    inventory_item_id="pi_001",
                ),
            ),
        )
        artifacts = Artifacts(original=original, normalized=artifact)
        application = KnowledgeReviewApplication(
            repository,
            Projection(),
            artifacts,  # type: ignore[arg-type]
        )

        comparison = await application.compare_review_item("krv_001", "pi_001")
        assert comparison.original.availability == "available"
        assert comparison.original.excerpt == target_text
        assert comparison.extracted.availability == "available"
        assert comparison.extracted.excerpt == target_text
        assert artifacts.original_read_calls == 0
        assert artifacts.original_excerpt_reads == [
            (
                ArtifactLocator("private/original"),
                "rev_001",
                canonical_sha256(original),
                start,
                start + len(excerpt),
                canonical_sha256(excerpt),
            )
        ]
        assert "private/original" not in repr(comparison)
        assert "private/normalized" not in repr(comparison)

    run(scenario())


def test_review_comparison_reports_changed_or_missing_artifact_without_provider_detail():
    async def scenario(original_error: Exception) -> None:
        original = b"exact clause"
        target = ReviewComparisonTarget(
            media_type="text/plain",
            source_revision_id="rev_001",
            source_digest=canonical_sha256(original),
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=ArtifactLocator("private/normalized"),
            item_id="pi_001",
            original_excerpt=OriginalExcerptRange(
                source_digest=canonical_sha256(original),
                start_byte=0,
                end_byte=len(original),
                excerpt_digest=canonical_sha256(original),
            ),
            original_alignment_reason=None,
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        artifacts = Artifacts(  # type: ignore[arg-type]
            original=original_error,
            normalized=ArtifactUnavailable("provider detail"),
        )
        application = KnowledgeReviewApplication(
            repository,
            Projection(),
            artifacts,
        )

        comparison = await application.compare_review_item("krv_001", "pi_001")
        assert comparison.original.availability == "unavailable"
        assert comparison.original.reason == "original-preview-unavailable"
        assert artifacts.original_read_calls == 0
        assert len(artifacts.original_excerpt_reads) == 1
        assert comparison.extracted.availability == "unavailable"
        assert comparison.extracted.reason == "extraction-preview-unavailable"

    run(scenario(ArtifactIntegrityFailure("changed digest")))
    run(scenario(ArtifactUnavailable("missing artifact")))


def test_review_comparison_keeps_old_and_unalignable_inventory_explicit():
    async def scenario(reason: str | None, expected: str) -> None:
        target = ReviewComparisonTarget(
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            source_revision_id="rev_001",
            source_digest=DIGEST,
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=None,
            item_id="pi_001",
            original_excerpt=None,
            original_alignment_reason=reason,
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        artifacts = Artifacts(original=b"unused", normalized=ArtifactUnavailable("unused"))
        comparison = await KnowledgeReviewApplication(
            repository,
            Projection(),
            artifacts,  # type: ignore[arg-type]
        ).compare_review_item("krv_001", "pi_001")
        assert comparison.original.availability == expected
        assert comparison.original.reason == (
            reason if reason is not None else "item-aligned-original-unavailable"
        )
        assert artifacts.original_read_calls == 0
        assert artifacts.original_excerpt_reads == []

    run(scenario(None, "unavailable"))
    run(scenario("source-text-not-stably-addressable", "unsupported"))


def test_review_original_download_checks_source_integrity_and_item_membership():
    async def scenario() -> None:
        original = b"%PDF-1.4\nexample"
        target = ReviewComparisonTarget(
            media_type="application/pdf",
            source_revision_id="rev_001",
            source_digest=canonical_sha256(original),
            original_locator=ArtifactLocator("private/original"),
            normalized_locator=None,
            item_id="pi_001",
            original_excerpt=None,
            original_alignment_reason="pdf-layout",
        )
        repository = ComparisonRepository(target)
        await repository.add(review(), inventory_item_ids=("pi_001",))
        artifacts = Artifacts(original=original, normalized=ArtifactUnavailable("unused"))
        application = KnowledgeReviewApplication(repository, Projection(), artifacts)  # type: ignore[arg-type]
        assert await application.read_original("krv_001", "pi_001") == (original, "application/pdf")
        import pytest

        from tap.modules.knowledge.application.review import ReviewNotFound

        with pytest.raises(ReviewNotFound):
            await application.read_original("krv_001", "pi_other")
        artifacts.original = b"changed"
        with pytest.raises(ReviewNotFound):
            await application.read_original("krv_001", "pi_001")

    run(scenario())
