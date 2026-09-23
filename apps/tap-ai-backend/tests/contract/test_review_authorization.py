from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tap.modules.access.application.authorize import authorize_project_action
from tap.modules.access.domain.authorization import ProjectPrincipal, ResourceRef

NOW = datetime(2026, 9, 23, 8, tzinfo=UTC)
REVIEW = ResourceRef(
    enterprise_id="synthetic-enterprise",
    project_id="synthetic-commerce-project",
    kind="knowledge-review",
    resource_id="review-001",
)


def principal(**changes) -> ProjectPrincipal:
    return replace(
        ProjectPrincipal(
            enterprise_id="synthetic-enterprise",
            project_id="synthetic-commerce-project",
            actor_id="synthetic-reviewer-02",
            audience="tap-ai",
            expires_at=NOW + timedelta(minutes=10),
            actions=frozenset(
                {
                    "knowledge.review.edit",
                    "knowledge.review.approve",
                    "knowledge.original.read",
                    "knowledge.citation.read",
                    "knowledge.evidence.read",
                }
            ),
            enabled=True,
        ),
        **changes,
    )


def test_review_action_allows_current_project_principal():
    decision = authorize_project_action(
        principal(),
        "knowledge.review.approve",
        REVIEW,
        expected_audience="tap-ai",
        now=NOW,
        separation_actor_id="synthetic-editor-01",
    )

    assert decision.allowed
    assert decision.reason == "project-action-allowed"


@pytest.mark.parametrize(
    "changed,expected",
    [
        ({"audience": "tap"}, "audience-mismatch"),
        ({"project_id": "other-project"}, "scope-mismatch"),
        ({"enabled": False}, "principal-disabled"),
        ({"expires_at": NOW}, "principal-expired"),
        ({"actions": frozenset()}, "action-not-allowed"),
    ],
)
def test_review_action_fails_closed_for_invalid_current_authority(changed, expected):
    decision = authorize_project_action(
        principal(**changed),
        "knowledge.review.approve",
        REVIEW,
        expected_audience="tap-ai",
        now=NOW,
        separation_actor_id="synthetic-editor-01",
    )

    assert not decision.allowed
    assert decision.reason == expected


def test_review_approval_rejects_the_actor_who_last_edited_the_revision():
    decision = authorize_project_action(
        principal(),
        "knowledge.review.approve",
        REVIEW,
        expected_audience="tap-ai",
        now=NOW,
        separation_actor_id="synthetic-reviewer-02",
    )

    assert not decision.allowed
    assert decision.reason == "separation-of-duties"


@pytest.mark.parametrize(
    "action,kind",
    [
        ("knowledge.original.read", "knowledge-original"),
        ("knowledge.citation.read", "knowledge-citation"),
        ("knowledge.evidence.read", "knowledge-evidence"),
    ],
)
def test_download_surfaces_require_their_exact_action_and_resource_kind(action, kind):
    resource = replace(REVIEW, kind=kind)
    assert authorize_project_action(
        principal(), action, resource, expected_audience="tap-ai", now=NOW
    ).allowed

    bypass = authorize_project_action(
        principal(actions=frozenset({"knowledge.review.approve"})),
        action,
        resource,
        expected_audience="tap-ai",
        now=NOW,
    )
    assert not bypass.allowed
    assert bypass.reason == "action-not-allowed"
