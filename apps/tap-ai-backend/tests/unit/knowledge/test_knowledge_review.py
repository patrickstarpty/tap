from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.knowledge.application.review import (
    InMemoryKnowledgeReviewRepository,
    KnowledgeReviewApplication,
    ProjectionNotReady,
    ReviewCommandConflict,
    ReviewStateConflict,
)
from tap.modules.knowledge.domain.review import (
    KnowledgeReviewRevision,
    ReviewCheckKind,
    ReviewDecisionStatus,
    ReviewStatus,
    canonical_digest,
    review_id_for,
)

NOW = datetime(2026, 9, 23, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def review(**changes: object) -> KnowledgeReviewRevision:
    base = KnowledgeReviewRevision(
        review_id="krv_001",
        project_id="synthetic-commerce-project",
        source_revision_ids=("rev_001",),
        inventory_digest=DIGEST_A,
        chunk_manifest_digest=DIGEST_B,
        annotation_digest=DIGEST_C,
        dependency_digest=DIGEST_A,
        editor_actor_ids=("synthetic-editor-01",),
        reviewer_actor_id=None,
        expires_at=NOW + timedelta(days=30),
        status=ReviewStatus.REVIEWING,
        version=3,
        blocking_item_ids=(),
        approved_item_ids=("pi_001",),
    )
    return replace(base, **changes)


class ProjectionGate:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[tuple[str, str]] = []

    async def verify(self, revision: KnowledgeReviewRevision, generation: str) -> bool:
        self.calls.append((revision.review_id, generation))
        return self.ready


class OpenReviewRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def resolve_open_review_target(self, **values: object) -> str:
        return review_id_for("synthetic-commerce-project", (str(values["source_revision_id"]),))

    async def create_or_open_review(self, **values: object) -> KnowledgeReviewRevision:
        self.calls.append(values)
        return review(
            review_id=review_id_for(
                "synthetic-commerce-project", (str(values["source_revision_id"]),)
            ),
            source_revision_ids=(str(values["source_revision_id"]),),
            editor_actor_ids=(str(values["actor_id"]),),
            reviewer_actor_id=None,
            expires_at=values["expires_at"],
            status=ReviewStatus.CHECKING,
            version=1,
        )


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_open_review_uses_server_actor_and_idempotent_intent_only():
    async def scenario() -> None:
        repository = OpenReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())  # type: ignore[arg-type]

        opened = await application.open_review(
            document_id="doc_001",
            source_revision_id="rev_001",
            authorized_review_id=review_id_for("synthetic-commerce-project", ("rev_001",)),
            actor_id="synthetic-editor-01",
            idempotency_key="open-review-001",
            now=NOW,
        )

        assert opened.review_id == review_id_for("synthetic-commerce-project", ("rev_001",))
        assert opened.status is ReviewStatus.CHECKING
        assert opened.editor_actor_ids == ("synthetic-editor-01",)
        assert repository.calls == [
            {
                "document_id": "doc_001",
                "source_revision_id": "rev_001",
                "authorized_review_id": review_id_for("synthetic-commerce-project", ("rev_001",)),
                "actor_id": "synthetic-editor-01",
                "expires_at": NOW + timedelta(days=30),
                "command_key": "open-review-001",
                "command_digest": canonical_digest(
                    {
                        "actorId": "synthetic-editor-01",
                        "documentId": "doc_001",
                        "operation": "open-review",
                        "sourceRevisionId": "rev_001",
                    }
                ),
                "now": NOW,
            }
        ]

    run(scenario())


def test_review_identity_is_stable_for_the_project_revision_set():
    assert review_id_for("project-001", ("rev-b", "rev-a")) == review_id_for(
        "project-001", ("rev-a", "rev-b")
    )
    assert review_id_for("project-002", ("rev-a", "rev-b")) != review_id_for(
        "project-001", ("rev-a", "rev-b")
    )


def test_review_approval_rejects_self_review_blockers_expiry_and_stale_version():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())

        for candidate, actor, expected in (
            (review(), "synthetic-editor-01", "separation-of-duties"),
            (
                review(blocking_item_ids=("pi_failed",)),
                "synthetic-reviewer-02",
                "review-has-blockers",
            ),
            (
                review(expires_at=NOW),
                "synthetic-reviewer-02",
                "review-expired",
            ),
            (
                review(approved_item_ids=()),
                "synthetic-reviewer-02",
                "review-has-no-approved-items",
            ),
        ):
            await repository.add(candidate)
            with pytest.raises(ReviewStateConflict, match=expected):
                await application.approve_review(
                    candidate.review_id,
                    actor_id=actor,
                    expected_version=candidate.version,
                    now=NOW,
                )
            await repository.clear()

        await repository.add(review())
        with pytest.raises(ReviewStateConflict, match="revision-conflict"):
            await application.approve_review(
                "krv_001",
                actor_id="synthetic-reviewer-02",
                expected_version=2,
                now=NOW,
            )

    run(scenario())


def test_concurrent_approval_accepts_only_one_optimistic_revision():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(review())

        results = await asyncio.gather(
            application.approve_review(
                "krv_001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            ),
            application.approve_review(
                "krv_001",
                actor_id="synthetic-reviewer-03",
                expected_version=3,
                now=NOW,
            ),
            return_exceptions=True,
        )

        assert sum(isinstance(result, KnowledgeReviewRevision) for result in results) == 1
        assert sum(isinstance(result, ReviewStateConflict) for result in results) == 1
        approved = await repository.get_review("krv_001")
        assert approved is not None
        assert approved.status is ReviewStatus.APPROVED
        assert approved.version == 4

    run(scenario())


def test_review_progression_is_ordered_and_return_to_checking_records_editor():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.DRAFT,
                version=1,
                editor_actor_ids=("synthetic-editor-01",),
            )
        )

        checking = await application.transition_review(
            "krv_001",
            target=ReviewStatus.CHECKING,
            actor_id="synthetic-editor-01",
            expected_version=1,
        )
        reviewing = await application.transition_review(
            "krv_001",
            target=ReviewStatus.REVIEWING,
            actor_id="synthetic-editor-01",
            expected_version=2,
        )
        returned = await application.transition_review(
            "krv_001",
            target=ReviewStatus.CHECKING,
            actor_id="synthetic-editor-03",
            expected_version=3,
        )

        assert checking.status is ReviewStatus.CHECKING
        assert reviewing.status is ReviewStatus.REVIEWING
        assert returned.status is ReviewStatus.CHECKING
        assert returned.editor_actor_ids == ("synthetic-editor-01", "synthetic-editor-03")
        assert returned.reviewer_actor_id is None
        with pytest.raises(ReviewStateConflict, match="invalid-review-transition"):
            await application.transition_review(
                "krv_001",
                target=ReviewStatus.PUBLISHED,
                actor_id="synthetic-editor-03",
                expected_version=4,
            )

    run(scenario())


def test_submit_rejects_an_empty_approved_scope():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(review(status=ReviewStatus.CHECKING, approved_item_ids=()))

        with pytest.raises(ReviewStateConflict, match="review-has-no-approved-items"):
            await application.transition_review(
                "krv_001",
                target=ReviewStatus.REVIEWING,
                actor_id="synthetic-editor-01",
                expected_version=3,
            )

    run(scenario())


def test_dependency_change_invalidates_approval_and_requires_new_review():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )

        changed = await application.record_dependency_change(
            "krv_001", dependency_digest=DIGEST_B, expected_version=3
        )

        assert changed.status is ReviewStatus.NEEDS_REVIEW
        assert changed.reviewer_actor_id is None
        assert changed.version == 4

    run(scenario())


def test_publish_verifies_projection_before_switch_and_replays_same_intent():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        gate = ProjectionGate()
        application = KnowledgeReviewApplication(repository, gate)
        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )

        publication = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-001",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )
        replay = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-001",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )

        assert replay == publication
        assert gate.calls == [("krv_001", "generation-001")]
        assert (await repository.current_publication("synthetic-commerce-project")) == publication
        assert publication.approved_item_ids == ("pi_001",)

        with pytest.raises(ReviewCommandConflict, match="idempotency-conflict"):
            await application.publish_review(
                "krv_001",
                generation="generation-002",
                idempotency_key="publish-001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            )

    run(scenario())


def test_publish_rejects_unapproved_or_expired_review_and_concurrent_replay_is_single():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        gate = ProjectionGate()
        application = KnowledgeReviewApplication(repository, gate)

        await repository.add(review(status=ReviewStatus.REVIEWING))
        with pytest.raises(ReviewStateConflict, match="review-not-approved"):
            await application.publish_review(
                "krv_001",
                generation="generation-001",
                idempotency_key="publish-001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            )

        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
                expires_at=NOW,
            )
        )
        with pytest.raises(ReviewStateConflict, match="review-expired"):
            await application.publish_review(
                "krv_001",
                generation="generation-001",
                idempotency_key="publish-001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            )

        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )
        first, replay = await asyncio.gather(
            application.publish_review(
                "krv_001",
                generation="generation-001",
                idempotency_key="publish-001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            ),
            application.publish_review(
                "krv_001",
                generation="generation-001",
                idempotency_key="publish-001",
                actor_id="synthetic-reviewer-02",
                expected_version=3,
                now=NOW,
            ),
        )

        assert first == replay
        assert await repository.current_publication("synthetic-commerce-project") == first

    run(scenario())


def test_failed_projection_keeps_previous_publication_visible():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        first = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                review_id="krv_old",
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )
        previous = await first.publish_review(
            "krv_old",
            generation="generation-old",
            idempotency_key="publish-old",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )
        await repository.add(
            review(
                review_id="krv_new",
                source_revision_ids=("rev_002",),
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-03",
            )
        )

        failing = KnowledgeReviewApplication(repository, ProjectionGate(ready=False))
        with pytest.raises(ProjectionNotReady):
            await failing.publish_review(
                "krv_new",
                generation="generation-new",
                idempotency_key="publish-new",
                actor_id="synthetic-reviewer-03",
                expected_version=3,
                now=NOW,
            )

        assert await repository.current_publication("synthetic-commerce-project") == previous

    run(scenario())


def test_withdraw_makes_publication_unreadable_before_projection_cleanup():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )
        publication = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-001",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )

        generation_started = asyncio.Event()
        withdrawal_committed = asyncio.Event()

        async def answer_generation() -> tuple[object, object]:
            before = await repository.current_publication("synthetic-commerce-project")
            generation_started.set()
            await withdrawal_committed.wait()
            after = await repository.current_publication("synthetic-commerce-project")
            return before, after

        async def withdraw():
            await generation_started.wait()
            result = await application.withdraw_publication(
                publication.publication_id,
                idempotency_key="withdraw-001",
                actor_id="synthetic-publisher-03",
                expected_version=1,
                now=NOW,
            )
            withdrawal_committed.set()
            return result

        (before, after), withdrawn = await asyncio.gather(answer_generation(), withdraw())

        assert withdrawn.status == "withdrawn"
        assert before == publication
        assert after is None
        assert await repository.current_publication("synthetic-commerce-project") is None
        assert await repository.pending_projection_cleanup() == ("generation-001",)

    run(scenario())


def test_item_decision_is_durable_updates_checklist_and_survives_application_refresh():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.CHECKING,
                blocking_item_ids=("pi_failed",),
            ),
            inventory_item_ids=("pi_001", "pi_failed"),
        )

        updated = await application.record_item_decision(
            "krv_001",
            item_id="pi_failed",
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.ACCEPTED,
            note="原件明确列为允许的例外",
            actor_id="synthetic-editor-03",
            expected_version=3,
            now=NOW,
        )

        refreshed = KnowledgeReviewApplication(repository, ProjectionGate())
        detail = await refreshed.get_review("krv_001")
        assert updated.version == 4
        assert updated.blocking_item_ids == ()
        assert updated.approved_item_ids == ("pi_001", "pi_failed")
        assert updated.editor_actor_ids == ("synthetic-editor-01", "synthetic-editor-03")
        assert len(detail.decisions) == 1
        assert detail.decisions[0].item_id == "pi_failed"
        assert detail.decisions[0].check_kind is ReviewCheckKind.EXCEPTION
        assert detail.decisions[0].status is ReviewDecisionStatus.ACCEPTED
        assert [entry.action for entry in detail.history][-1] == "item_decided"

    run(scenario())


def test_repeated_item_decisions_are_append_only_and_publication_remains_traceable():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.CHECKING,
                blocking_item_ids=("pi_001",),
                approved_item_ids=(),
            ),
            inventory_item_ids=("pi_001",),
        )

        await application.record_item_decision(
            "krv_001",
            item_id="pi_001",
            check_kind=ReviewCheckKind.AMOUNT,
            status=ReviewDecisionStatus.BLOCKED,
            note="金额与原件不一致",
            actor_id="synthetic-editor-01",
            expected_version=3,
            now=NOW,
        )
        await application.record_item_decision(
            "krv_001",
            item_id="pi_001",
            check_kind=ReviewCheckKind.EXCEPTION,
            status=ReviewDecisionStatus.ACCEPTED,
            note="第二次核对确认例外条款适用",
            actor_id="synthetic-editor-01",
            expected_version=4,
            now=NOW + timedelta(minutes=1),
        )
        await application.transition_review(
            "krv_001",
            target=ReviewStatus.REVIEWING,
            actor_id="synthetic-editor-01",
            expected_version=5,
        )
        approved = await application.approve_review(
            "krv_001",
            actor_id="synthetic-reviewer-02",
            expected_version=6,
            now=NOW + timedelta(minutes=2),
        )
        publication = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-decision-history",
            actor_id="synthetic-reviewer-02",
            expected_version=7,
            now=NOW + timedelta(minutes=3),
        )

        detail = await application.get_review("krv_001")
        assert [
            (item.review_version, item.status.value, item.note) for item in detail.decisions
        ] == [(5, "accepted", "第二次核对确认例外条款适用")]
        assert [
            (item.review_version, item.check_kind.value, item.status.value, item.note)
            for item in detail.decision_history
        ] == [
            (4, "amount", "blocked", "金额与原件不一致"),
            (5, "exception", "accepted", "第二次核对确认例外条款适用"),
        ]
        decision_events = [item for item in detail.history if item.action == "item_decided"]
        assert all(item.decision_id and item.decision_digest for item in decision_events)
        assert [item.decision_id for item in decision_events] == [
            item.decision_id for item in detail.decision_history
        ]
        assert publication.review_version == approved.version
        assert publication.approval_digest == approved.approval_digest

    run(scenario())


def test_item_decision_rejects_unknown_item_and_stale_version_without_mutation():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(review(status=ReviewStatus.CHECKING), inventory_item_ids=("pi_001",))

        for item_id, expected_version, expected in (
            ("pi_unknown", 3, "review-item-not-found"),
            ("pi_001", 2, "revision-conflict"),
        ):
            with pytest.raises(ReviewStateConflict, match=expected):
                await application.record_item_decision(
                    "krv_001",
                    item_id=item_id,
                    check_kind=ReviewCheckKind.AMOUNT,
                    status=ReviewDecisionStatus.BLOCKED,
                    note="金额与原件不一致",
                    actor_id="synthetic-editor-01",
                    expected_version=expected_version,
                    now=NOW,
                )
        current = await repository.get_review("krv_001")
        assert current is not None and current.version == 3
        assert await repository.list_decisions("krv_001") == ()

    run(scenario())


def test_publish_and_withdraw_reject_stale_versions_and_expose_current_vs_history():
    async def scenario() -> None:
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        await repository.add(
            review(
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="synthetic-reviewer-02",
            )
        )

        with pytest.raises(ReviewStateConflict, match="revision-conflict"):
            await application.publish_review(
                "krv_001",
                generation="generation-001",
                idempotency_key="publish-stale",
                actor_id="synthetic-reviewer-02",
                expected_version=2,
                now=NOW,
            )
        published = await application.publish_review(
            "krv_001",
            generation="generation-001",
            idempotency_key="publish-001",
            actor_id="synthetic-reviewer-02",
            expected_version=3,
            now=NOW,
        )
        assert published.version == 1
        with pytest.raises(ReviewStateConflict, match="revision-conflict"):
            await application.withdraw_publication(
                published.publication_id,
                idempotency_key="withdraw-stale",
                actor_id="synthetic-publisher-03",
                expected_version=2,
                now=NOW,
            )
        withdrawn = await application.withdraw_publication(
            published.publication_id,
            idempotency_key="withdraw-001",
            actor_id="synthetic-publisher-03",
            expected_version=1,
            now=NOW,
        )

        assert withdrawn.version == 2
        assert await repository.current_publication("synthetic-commerce-project") is None
        assert await repository.get_publication(published.publication_id) == withdrawn

    run(scenario())


def test_independent_publications_survive_publish_withdraw_and_expiry():
    from tap.modules.access.domain.policy import AuthorizationDenied
    from tap.modules.knowledge.application.publication import PublishedKnowledgeAuthority

    async def scenario():
        repository = InMemoryKnowledgeReviewRepository()
        application = KnowledgeReviewApplication(repository, ProjectionGate())
        publications = []
        for number in (1, 2):
            candidate = review(
                review_id=f"review-{number}",
                source_revision_ids=(f"revision-{number}",),
                approved_item_ids=(f"item-{number}",),
                status=ReviewStatus.APPROVED,
                reviewer_actor_id="reviewer",
                expires_at=NOW + timedelta(hours=number),
            )
            await repository.add(candidate)
            publications.append(
                await application.publish_review(
                    candidate.review_id,
                    generation="generation-1",
                    idempotency_key=f"pub-{number}",
                    actor_id="publisher",
                    expected_version=3,
                    now=NOW,
                )
            )
        authority = PublishedKnowledgeAuthority(repository, now=lambda: NOW)
        first = await authority.authorize_selection("synthetic-commerce-project", ("revision-1",))
        await authority.authorize_selection(
            "synthetic-commerce-project", ("revision-1", "revision-2")
        )
        assert (
            await application.current_publication_for_review(
                await repository.get_review("review-1")
            )
        ) == publications[0]
        replacement = review(
            review_id="review-2-replacement",
            source_revision_ids=("revision-2",),
            approved_item_ids=("item-2",),
            status=ReviewStatus.APPROVED,
            reviewer_actor_id="reviewer",
            expires_at=NOW + timedelta(hours=2),
        )
        await repository.add(replacement)
        old_second = publications[1]
        publications[1] = await application.publish_review(
            replacement.review_id,
            generation="generation-1",
            idempotency_key="replacement-2",
            actor_id="publisher",
            expected_version=3,
            now=NOW,
        )
        await authority.revalidate(first)
        with pytest.raises(ReviewStateConflict, match="publication-not-current"):
            await application.withdraw_publication(
                old_second.publication_id,
                idempotency_key="old-2",
                actor_id="publisher",
                expected_version=1,
                now=NOW,
            )
        expired = PublishedKnowledgeAuthority(repository, now=lambda: NOW + timedelta(hours=1))
        with pytest.raises(AuthorizationDenied):
            await expired.authorize_selection("synthetic-commerce-project", ("revision-1",))
        await expired.authorize_selection("synthetic-commerce-project", ("revision-2",))
        await application.withdraw_publication(
            publications[1].publication_id,
            idempotency_key="withdraw-2",
            actor_id="publisher",
            expected_version=1,
            now=NOW,
        )
        await authority.revalidate(first)
        assert await repository.pending_projection_cleanup() == ()
        with pytest.raises(AuthorizationDenied):
            await authority.authorize_selection("synthetic-commerce-project", ("revision-2",))

        await application.withdraw_publication(
            publications[0].publication_id,
            idempotency_key="withdraw-final",
            actor_id="publisher",
            expected_version=1,
            now=NOW,
        )
        assert set(await repository.pending_projection_cleanup()) == {"generation-1"}

    run(scenario())
