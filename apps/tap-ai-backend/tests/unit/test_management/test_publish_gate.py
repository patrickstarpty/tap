from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.test_management.application.publish import PublishTestPlan
from tap.modules.test_management.domain.models import (
    ReviewDisposition,
    RevisionStatus,
)
from tap.modules.test_management.domain.models import (
    TestPlanReviewDecision as PlanReviewDecision,
)
from tap.modules.test_management.domain.validation import RevisionConflict, RevisionImmutable

from .test_plan_revision import _revision


class Repository:
    def __init__(self):
        self.revision = _revision()
        self.published = []

    async def get_revision(self, scope, test_plan_id, revision_id):
        assert scope == VALIDATION_SCOPE
        assert test_plan_id == self.revision.test_plan_id
        assert revision_id == self.revision.revision_id
        return self.revision

    async def publish_revision(
        self, scope, revision_id, expected_version, validation_digest, idempotency_key
    ):
        assert idempotency_key
        if expected_version != self.revision.version:
            raise RevisionConflict("revision version changed")
        self.revision = replace(self.revision, status=RevisionStatus.PUBLISHED)
        self.published.append((scope, revision_id, validation_digest))
        return self.revision


class Citations:
    def __init__(
        self,
        authorized: bool = True,
        requirement_scope_current: bool = True,
        knowledge_versions_current: bool = True,
    ):
        self.authorized = authorized
        self.requirement_scope_current = requirement_scope_current
        self.knowledge_versions_current = knowledge_versions_current

    async def is_authorized(self, scope, citation):
        assert scope == VALIDATION_SCOPE
        return self.authorized

    async def is_requirement_scope_current(self, scope, revision):
        return self.requirement_scope_current

    async def are_knowledge_versions_current(self, scope, revision):
        return self.knowledge_versions_current


def _reviewed(repository: Repository, *, actor_id: str = "independent-reviewer") -> None:
    repository.revision = replace(
        repository.revision,
        author_actor_id="draft-author",
        review_decisions=(
            PlanReviewDecision(
                "review_checkout",
                ReviewDisposition.ACCEPTED_UNCHANGED,
                "Verified against the approved checkout requirement.",
                actor_id,
                repository.revision.content_digest,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_publish_requires_authorized_citations_and_expected_version() -> None:
    repository = Repository()
    _reviewed(repository)
    publisher = PublishTestPlan(repository, Citations())

    with pytest.raises(RevisionConflict):
        await publisher.execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=2,
            idempotency_key="publish-wrong-version",
        )

    repository = Repository()
    _reviewed(repository)
    with pytest.raises(ValueError, match="authorized"):
        await PublishTestPlan(repository, Citations(False)).execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-unauthorized",
        )
    assert repository.published == []


@pytest.mark.asyncio
async def test_publish_is_explicit_and_published_revision_is_immutable() -> None:
    repository = Repository()
    _reviewed(repository)
    publisher = PublishTestPlan(repository, Citations())

    published = await publisher.execute(
        VALIDATION_SCOPE,
        "tp_checkout",
        "tpr_checkout_v1",
        expected_version=1,
        idempotency_key="publish-checkout",
    )
    assert published.status is RevisionStatus.PUBLISHED
    assert repository.published[0][2].startswith("sha256:")

    with pytest.raises(RevisionImmutable):
        await publisher.execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-again",
        )


@pytest.mark.asyncio
async def test_publish_rechecks_review_requirement_scope_and_knowledge_versions() -> None:
    repository = Repository()
    with pytest.raises(ValueError, match="human review"):
        await PublishTestPlan(repository, Citations()).execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-without-review",
        )

    _reviewed(repository)
    with pytest.raises(ValueError, match="requirement scope"):
        await PublishTestPlan(repository, Citations(requirement_scope_current=False)).execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-stale-scope",
        )
    with pytest.raises(ValueError, match="knowledge"):
        await PublishTestPlan(repository, Citations(knowledge_versions_current=False)).execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-stale-knowledge",
        )


@pytest.mark.asyncio
async def test_strict_project_requires_non_author_business_review() -> None:
    repository = Repository()
    _reviewed(repository, actor_id="draft-author")
    repository.revision = replace(repository.revision, strict_review_required=True)

    with pytest.raises(ValueError, match="non-author"):
        await PublishTestPlan(repository, Citations()).execute(
            VALIDATION_SCOPE,
            "tp_checkout",
            "tpr_checkout_v1",
            expected_version=1,
            idempotency_key="publish-self-review",
        )
