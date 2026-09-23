from __future__ import annotations

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tests.conftest import validation_http_services

BASE = "/api/v1/projects/tapper-demo/knowledge/reviews/krv_001"
ORIGIN = "http://127.0.0.1:15175"


class ReviewHttpSpy:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE as scope

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def approve_review(self, review_id, expected_version):  # type: ignore[no-untyped-def]
        self.calls.append(("approve", review_id, expected_version))
        return review_payload(status="approved", version=4)

    async def publish_review(self, review_id, generation, key):  # type: ignore[no-untyped-def]
        self.calls.append(("publish", review_id, generation, key))
        return publication_payload()

    async def withdraw_publication(self, publication_id, key):  # type: ignore[no-untyped-def]
        self.calls.append(("withdraw", publication_id, key))
        return publication_payload(status="withdrawn")


def review_payload(*, status: str, version: int) -> dict[str, object]:
    return {
        "reviewId": "krv_001",
        "status": status,
        "version": version,
        "reviewerActorId": "synthetic-reviewer-02",
        "expiresAt": "2026-10-23T09:00:00+00:00",
        "approvalDigest": "sha256:" + "a" * 64,
    }


def publication_payload(*, status: str = "published") -> dict[str, object]:
    return {
        "publicationId": "kpb_001",
        "reviewId": "krv_001",
        "reviewVersion": 4,
        "status": status,
        "generation": "generation-001",
        "approvalDigest": "sha256:" + "a" * 64,
        "sourceRevisionIds": ["rev_001"],
        "approvedItemIds": ["pi_001"],
        "publishedAt": "2026-09-23T09:00:00+00:00",
        "expiresAt": "2026-10-23T09:00:00+00:00",
    }


def client() -> tuple[TestClient, ReviewHttpSpy]:
    spy = ReviewHttpSpy()
    base = validation_http_services()
    services = HttpServices(
        knowledge=base.knowledge,
        readiness=base.readiness,
        scope_provider=base.scope_provider,
        authorization_policy=base.authorization_policy,
        scope=base.scope,
        knowledge_reviews=spy,
    )
    return TestClient(create_app(services=services, allowed_origins=frozenset({ORIGIN}))), spy


def test_review_http_preserves_optimistic_version_and_idempotent_publish_intent():
    http, spy = client()
    approved = http.post(BASE + "/approve", headers={"Origin": ORIGIN, "If-Match": '"3"'})
    published = http.post(
        BASE + "/publish",
        headers={"Origin": ORIGIN, "Idempotency-Key": "publish-001"},
        json={"generation": "generation-001"},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert published.status_code == 200
    assert published.json()["approvedItemIds"] == ["pi_001"]
    assert spy.calls == [
        ("approve", "krv_001", 3),
        ("publish", "krv_001", "generation-001", "publish-001"),
    ]


def test_review_http_rejects_missing_or_malformed_preconditions_before_execution():
    http, spy = client()
    for headers in (
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "If-Match": "3"},
        {"Origin": ORIGIN, "If-Match": '"0"'},
    ):
        assert http.post(BASE + "/approve", headers=headers).status_code == 422
    assert spy.calls == []


def test_publication_withdrawal_is_project_scoped_and_requires_idempotency():
    http, spy = client()
    path = "/api/v1/projects/tapper-demo/knowledge/publications/kpb_001/withdraw"
    assert http.post(path, headers={"Origin": ORIGIN}).status_code == 422
    response = http.post(
        path,
        headers={"Origin": ORIGIN, "Idempotency-Key": "withdraw-001"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert spy.calls == [("withdraw", "kpb_001", "withdraw-001")]
    assert http.post(path.replace("tapper-demo", "foreign")).status_code == 403


def test_review_routes_are_in_the_canonical_project_api():
    paths = create_app().openapi()["paths"]
    review = "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}"
    publication = "/api/v1/projects/{project_id}/knowledge/publications/{publication_id}"
    assert "post" in paths[review + "/approve"]
    assert "post" in paths[review + "/publish"]
    assert "post" in paths[publication + "/withdraw"]


def test_review_authority_tables_scope_external_keys_and_publication_links():
    from sqlalchemy import UniqueConstraint

    from tap.platform.db.registry import load_authoritative_metadata

    tables = load_authoritative_metadata().tables
    command = tables["knowledge_review_command"]
    assert "command_id" in command.c
    assert command.c.idempotency_key.unique is not True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns.keys()) == ("project_id", "idempotency_key")
        for constraint in command.constraints
    )
    for table_name in (
        "knowledge_publication",
        "knowledge_current_publication",
        "knowledge_publication_cleanup",
    ):
        assert any(
            {element.parent.name for element in constraint.elements}
            & {"review_id", "publication_id"}
            for constraint in tables[table_name].foreign_key_constraints
            if len(constraint.elements) == 2
        )
    assert tables["knowledge_current_publication"].c.pointer_id.type.length == 128
