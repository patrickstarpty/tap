from dataclasses import replace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.test_management.application.publish import PublishTestPlan
from tap.modules.test_management.domain.models import RevisionStatus
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

    async def publish_revision(self, scope, revision_id, expected_version, validation_digest):
        if expected_version != self.revision.version:
            raise RevisionConflict("revision version changed")
        self.revision = replace(self.revision, status=RevisionStatus.PUBLISHED)
        self.published.append((scope, revision_id, validation_digest))
        return self.revision


class Citations:
    def __init__(self, authorized: bool = True):
        self.authorized = authorized

    async def is_authorized(self, scope, citation):
        assert scope == VALIDATION_SCOPE
        return self.authorized


@pytest.mark.asyncio
async def test_publish_requires_authorized_citations_and_expected_version() -> None:
    repository = Repository()
    publisher = PublishTestPlan(repository, Citations())

    with pytest.raises(RevisionConflict):
        await publisher.execute(
            VALIDATION_SCOPE, "tp_checkout", "tpr_checkout_v1", expected_version=2
        )

    repository = Repository()
    with pytest.raises(ValueError, match="authorized"):
        await PublishTestPlan(repository, Citations(False)).execute(
            VALIDATION_SCOPE, "tp_checkout", "tpr_checkout_v1", expected_version=1
        )
    assert repository.published == []


@pytest.mark.asyncio
async def test_publish_is_explicit_and_published_revision_is_immutable() -> None:
    repository = Repository()
    publisher = PublishTestPlan(repository, Citations())

    published = await publisher.execute(
        VALIDATION_SCOPE, "tp_checkout", "tpr_checkout_v1", expected_version=1
    )
    assert published.status is RevisionStatus.PUBLISHED
    assert repository.published[0][2].startswith("sha256:")

    with pytest.raises(RevisionImmutable):
        await publisher.execute(
            VALIDATION_SCOPE, "tp_checkout", "tpr_checkout_v1", expected_version=1
        )
