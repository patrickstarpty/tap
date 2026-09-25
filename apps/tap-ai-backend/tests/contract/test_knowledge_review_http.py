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

    async def list_reviews(self, source_revision_id):  # type: ignore[no-untyped-def]
        self.calls.append(("list", source_revision_id))
        return {"items": [review_detail_payload()]}

    async def get_review(self, review_id):  # type: ignore[no-untyped-def]
        self.calls.append(("get", review_id))
        return review_detail_payload()

    async def get_publication(self, publication_id):  # type: ignore[no-untyped-def]
        self.calls.append(("get-publication", publication_id))
        return publication_payload()

    async def get_current_publication(self):
        self.calls.append(("get-current-publication",))
        return publication_payload()

    async def list_published_sources(self):
        self.calls.append(("published-sources",))
        return {
            "items": [
                {
                    "sourceId": "src_" + "1" * 32,
                    "documentId": "doc_001",
                    "revisionId": "rev_001",
                    "sourceName": "退款规则",
                    "filename": "refund.md",
                    "publicationId": "kpb_001",
                    "expiresAt": "2026-10-23T09:00:00+00:00",
                    "approvedItemCount": 1,
                    "inventoryItemCount": 2,
                    "partial": True,
                }
            ]
        }

    async def compare_review_item(self, review_id, item_id):  # type: ignore[no-untyped-def]
        self.calls.append(("compare", review_id, item_id))
        return {
            "reviewId": review_id,
            "itemId": item_id,
            "original": {"availability": "unsupported", "reason": "preview-not-supported"},
            "extracted": {"availability": "unavailable", "reason": "not-extracted"},
        }

    async def update_item_decision(  # type: ignore[no-untyped-def]
        self, review_id, item_id, body, expected_version
    ):
        self.calls.append(("decide", review_id, item_id, body, expected_version))
        return review_detail_payload(version=4)

    async def return_review(self, review_id, expected_version):  # type: ignore[no-untyped-def]
        self.calls.append(("return", review_id, expected_version))
        return review_detail_payload(status="checking", version=4)

    async def submit_review(self, review_id, expected_version):  # type: ignore[no-untyped-def]
        self.calls.append(("submit", review_id, expected_version))
        return review_detail_payload(status="reviewing", version=4)

    async def approve_review(self, review_id, expected_version):  # type: ignore[no-untyped-def]
        self.calls.append(("approve", review_id, expected_version))
        return review_payload(status="approved", version=4)

    async def publish_review(  # type: ignore[no-untyped-def]
        self, review_id, generation, expected_version, key
    ):
        self.calls.append(("publish", review_id, generation, expected_version, key))
        return publication_payload()

    async def withdraw_publication(  # type: ignore[no-untyped-def]
        self, publication_id, expected_version, key
    ):
        self.calls.append(("withdraw", publication_id, expected_version, key))
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
        "version": 1 if status == "published" else 2,
        "status": status,
        "generation": "generation-001",
        "approvalDigest": "sha256:" + "a" * 64,
        "sourceRevisionIds": ["rev_001"],
        "approvedItemIds": ["pi_001"],
        "publishedAt": "2026-09-23T09:00:00+00:00",
        "expiresAt": "2026-10-23T09:00:00+00:00",
        "withdrawnBy": "synthetic-publisher-03" if status == "withdrawn" else None,
        "withdrawnAt": "2026-09-24T09:00:00+00:00" if status == "withdrawn" else None,
    }


def review_detail_payload(*, status: str = "checking", version: int = 3) -> dict[str, object]:
    return {
        **review_payload(status=status, version=version),
        "sourceRevisionIds": ["rev_001"],
        "editorActorIds": ["synthetic-editor-01"],
        "blockingItemIds": ["pi_failed"],
        "approvedItemIds": ["pi_001"],
        "inventory": {
            "items": [
                {
                    "sourceRevisionId": "rev_001",
                    "itemId": "pi_001",
                    "attempt": 2,
                    "kind": "paragraph",
                    "locator": "paragraph:1",
                    "status": "parsed",
                    "artifactDigest": "sha256:" + "b" * 64,
                    "reason": None,
                    "decisionActorId": None,
                },
                {
                    "sourceRevisionId": "rev_001",
                    "itemId": "pi_failed",
                    "attempt": 2,
                    "kind": "image",
                    "locator": "page:2/image:1",
                    "status": "failed",
                    "artifactDigest": "sha256:" + "c" * 64,
                    "reason": "ocr-required",
                    "decisionActorId": None,
                },
            ],
            "parsedCount": 1,
            "failedCount": 1,
            "needsReviewCount": 0,
            "excludedCount": 0,
        },
        "decisions": [],
        "history": [],
        "currentPublication": None,
        "publicationTarget": {
            "status": "ready",
            "generation": "generation-001",
            "reason": None,
        },
        "allowedActions": ["edit", "submit", "read_original"],
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
        headers={
            "Origin": ORIGIN,
            "If-Match": '"4"',
            "Idempotency-Key": "publish-001",
        },
        json={"generation": "generation-001"},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert published.status_code == 200
    assert published.json()["approvedItemIds"] == ["pi_001"]
    assert spy.calls == [
        ("approve", "krv_001", 3),
        ("publish", "krv_001", "generation-001", 4, "publish-001"),
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
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "withdraw-001",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"
    assert spy.calls == [("withdraw", "kpb_001", 1, "withdraw-001")]
    assert http.post(path.replace("tapper-demo", "foreign")).status_code == 403


def test_review_routes_are_in_the_canonical_project_api():
    paths = create_app().openapi()["paths"]
    review = "/api/v1/projects/{project_id}/knowledge/reviews/{review_id}"
    publication = "/api/v1/projects/{project_id}/knowledge/publications/{publication_id}"
    assert "post" in paths[review + "/approve"]
    assert "post" in paths[review + "/publish"]
    assert "post" in paths[publication + "/withdraw"]
    assert "get" in paths[review]
    assert "get" in paths[publication]
    assert "get" in paths["/api/v1/projects/{project_id}/knowledge/publications/current"]
    assert "get" in paths["/api/v1/projects/{project_id}/knowledge/published-sources"]


def test_review_read_model_survives_refresh_and_exposes_partial_failure_and_capabilities():
    http, spy = client()

    listed = http.get(
        "/api/v1/projects/tapper-demo/knowledge/reviews",
        params={"sourceRevisionId": "rev_001"},
    )
    refreshed = http.get(BASE)
    comparison = http.get(BASE + "/items/pi_failed/comparison")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["inventory"] == {
        "items": listed.json()["items"][0]["inventory"]["items"],
        "parsedCount": 1,
        "failedCount": 1,
        "needsReviewCount": 0,
        "excludedCount": 0,
    }
    assert refreshed.status_code == 200
    assert refreshed.json()["allowedActions"] == ["edit", "submit", "read_original"]
    assert refreshed.json()["publicationTarget"]["generation"] == "generation-001"
    assert comparison.status_code == 200
    assert comparison.json()["original"]["availability"] == "unsupported"
    assert comparison.json()["extracted"]["availability"] == "unavailable"
    assert spy.calls == [
        ("list", "rev_001"),
        ("get", "krv_001"),
        ("compare", "krv_001", "pi_failed"),
    ]


def test_review_mutations_all_carry_versions_and_return_reloadable_state():
    http, spy = client()
    headers = {"Origin": ORIGIN, "If-Match": '"3"'}

    decided = http.put(
        BASE + "/items/pi_001/decision",
        headers=headers,
        json={"checkKind": "amount", "status": "accepted", "note": "与原件一致"},
    )
    returned = http.post(BASE + "/return", headers=headers)
    submitted = http.post(BASE + "/submit", headers=headers)

    assert decided.status_code == returned.status_code == submitted.status_code == 200
    assert decided.json()["version"] == 4
    assert returned.json()["status"] == "checking"
    assert submitted.json()["status"] == "reviewing"
    assert spy.calls == [
        (
            "decide",
            "krv_001",
            "pi_001",
            {"checkKind": "amount", "status": "accepted", "note": "与原件一致"},
            3,
        ),
        ("return", "krv_001", 3),
        ("submit", "krv_001", 3),
    ]


def test_current_publication_and_picker_do_not_infer_authority_from_ingestion_ready():
    http, spy = client()

    current = http.get("/api/v1/projects/tapper-demo/knowledge/publications/current")
    picker = http.get("/api/v1/projects/tapper-demo/knowledge/published-sources")
    historical = http.get("/api/v1/projects/tapper-demo/knowledge/publications/kpb_001")

    assert current.status_code == historical.status_code == picker.status_code == 200
    assert picker.json()["items"] == [
        {
            "sourceId": "src_" + "1" * 32,
            "documentId": "doc_001",
            "revisionId": "rev_001",
            "sourceName": "退款规则",
            "filename": "refund.md",
            "publicationId": "kpb_001",
            "expiresAt": "2026-10-23T09:00:00+00:00",
            "approvedItemCount": 1,
            "inventoryItemCount": 2,
            "partial": True,
        }
    ]
    assert spy.calls == [
        ("get-current-publication",),
        ("published-sources",),
        ("get-publication", "kpb_001"),
    ]


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
