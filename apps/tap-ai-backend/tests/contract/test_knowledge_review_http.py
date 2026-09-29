from __future__ import annotations

from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.knowledge.application.review import ReviewStateConflict
from tap.modules.knowledge.domain.review import review_id_for
from tests.conftest import validation_http_services

BASE = "/api/v1/projects/tapper-demo/knowledge/reviews/krv_001"
ORIGIN = "http://127.0.0.1:15175"


class ReviewHttpSpy:
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE as scope

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.open_target_review_id = review_id_for("tapper-demo", ("rev_001",))
        self.open_target_conflict: str | None = None

    async def get_flowchart(self, review_id):
        self.calls.append(("flowchart", review_id))
        return {
            "nodes": [{"id": "a", "label": "开始", "lane": "", "box": [0, 0, 10, 10]}],
            "edges": [],
        }

    async def correct_flowchart(self, review_id, graph, expected_version):
        self.calls.append(("correct-flowchart", review_id, graph, expected_version))
        return {"sourceRevisionId": "rev_corrected"}

    async def resolve_open_review(self, document_id, source_revision_id):  # type: ignore[no-untyped-def]
        self.calls.append(("resolve-open", document_id, source_revision_id))
        if self.open_target_conflict is not None:
            raise ReviewStateConflict(self.open_target_conflict)
        return self.open_target_review_id

    async def open_review(  # type: ignore[no-untyped-def]
        self, document_id, source_revision_id, authorized_review_id, key
    ):
        self.calls.append(("open", document_id, source_revision_id, authorized_review_id, key))
        return {
            **review_detail_payload(status="checking", version=1),
            "reviewId": authorized_review_id,
        }

    async def list_reviews(  # type: ignore[no-untyped-def]
        self, source_revision_id, limit=50, after_review_id=None
    ):
        self.calls.append(("list", source_revision_id, limit, after_review_id))
        return {"items": [review_detail_payload()], "nextCursor": None}

    async def get_review(self, review_id):  # type: ignore[no-untyped-def]
        self.calls.append(("get", review_id))
        return review_detail_payload()

    async def get_review_inventory(  # type: ignore[no-untyped-def]
        self, review_id, limit=100, after_item_id=None
    ):
        self.calls.append(("inventory", review_id, limit, after_item_id))
        return {
            "items": review_detail_payload()["inventory"]["items"][:1],
            "totalCount": 2,
            "parsedCount": 1,
            "failedCount": 1,
            "needsReviewCount": 0,
            "excludedCount": 0,
            "nextCursor": "pi_001",
        }

    async def list_review_decision_history(  # type: ignore[no-untyped-def]
        self, review_id, limit=100, after_version=None
    ):
        self.calls.append(("decision-history", review_id, limit, after_version))
        return {"items": [], "totalCount": 503, "nextCursor": 501}

    async def list_review_history(  # type: ignore[no-untyped-def]
        self, review_id, limit=100, after_version=None
    ):
        self.calls.append(("history", review_id, limit, after_version))
        return {"items": [], "totalCount": 507, "nextCursor": 500}

    async def list_review_publications(  # type: ignore[no-untyped-def]
        self, review_id, limit=100, after_publication_id=None
    ):
        self.calls.append(("publications", review_id, limit, after_publication_id))
        return {
            "items": [publication_payload()],
            "totalCount": 502,
            "nextCursor": "kpb_001",
        }

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

    async def read_original(self, review_id, item_id):
        self.calls.append(("original", review_id, item_id))
        return b"%PDF-1.4 source", "application/pdf"

    async def compare_review_item(self, review_id, item_id):  # type: ignore[no-untyped-def]
        self.calls.append(("compare", review_id, item_id))
        return {
            "reviewId": review_id,
            "itemId": item_id,
            "original": {"availability": "unsupported", "reason": "preview-not-supported"},
            "extracted": {"availability": "unavailable", "reason": "not-extracted"},
        }

    async def read_original_image(self, review_id, item_id):  # type: ignore[no-untyped-def]
        self.calls.append(("original-image", review_id, item_id))
        return b"\x89PNG\r\n\x1a\n", "image/png"

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
            "totalCount": 2,
            "parsedCount": 1,
            "failedCount": 1,
            "needsReviewCount": 0,
            "excludedCount": 0,
        },
        "decisions": [],
        "decisionHistory": [],
        "decisionHistoryTotalCount": 0,
        "decisionHistoryNextCursor": None,
        "history": [],
        "historyTotalCount": 0,
        "historyNextCursor": None,
        "publicationIds": ["kpb_historical", "kpb_001"],
        "publicationTotalCount": 2,
        "publicationNextCursor": None,
        "currentPublication": None,
        "publicationTarget": {
            "status": "ready",
            "generation": "generation-001",
            "reason": None,
        },
        "allowedActions": ["edit", "submit", "read_original"],
    }


class RecordingPolicy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        self.calls.append((action, resource.kind, resource.resource_id))
        return AuthorizationDecision(True, "test-allowed")


class DenyPolicy(RecordingPolicy):
    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        self.calls.append((action, resource.kind, resource.resource_id))
        return AuthorizationDecision(False, "test-denied")


class ReviewResourcePolicy(RecordingPolicy):
    def __init__(self, *allowed_review_ids: str) -> None:
        super().__init__()
        self.allowed_review_ids = frozenset(allowed_review_ids)

    async def authorize(self, scope, action, resource):  # type: ignore[no-untyped-def]
        self.calls.append((action, resource.kind, resource.resource_id))
        return AuthorizationDecision(
            (
                action == "knowledge.read"
                and resource.kind == "knowledge"
                and resource.resource_id == "doc_001"
            )
            or (
                action == "knowledge.review.edit"
                and resource.kind == "knowledge-review"
                and resource.resource_id in self.allowed_review_ids
            ),
            "resource-match",
        )


def client(policy=None) -> tuple[TestClient, ReviewHttpSpy]:  # type: ignore[no-untyped-def]
    spy = ReviewHttpSpy()
    base = validation_http_services()
    services = HttpServices(
        knowledge=base.knowledge,
        readiness=base.readiness,
        scope_provider=base.scope_provider,
        authorization_policy=base.authorization_policy if policy is None else policy,
        scope=base.scope,
        knowledge_reviews=spy,
    )
    return TestClient(create_app(services=services, allowed_origins=frozenset({ORIGIN}))), spy


def test_route_authorization_uses_the_same_review_and_publication_resource_ids_as_actions():
    policy = RecordingPolicy()
    http, _ = client(policy)

    decided = http.put(
        BASE + "/items/pi_001/decision",
        headers={"Origin": ORIGIN, "If-Match": '"3"'},
        json={"checkKind": "amount", "status": "accepted", "note": "与原件一致"},
    )
    withdrawn = http.post(
        "/api/v1/projects/tapper-demo/knowledge/publications/kpb_001/withdraw",
        headers={
            "Origin": ORIGIN,
            "If-Match": '"1"',
            "Idempotency-Key": "withdraw-resource-target",
        },
    )

    assert decided.status_code == withdrawn.status_code == 200
    assert (
        "knowledge.review.edit",
        "knowledge-review",
        "krv_001",
    ) in policy.calls
    assert (
        "knowledge.publish",
        "knowledge-publication",
        "kpb_001",
    ) in policy.calls


def test_original_comparison_permission_denial_never_reaches_artifact_service():
    policy = DenyPolicy()
    http, spy = client(policy)

    response = http.get(BASE + "/items/pi_001/comparison")

    assert response.status_code == 403
    assert spy.calls == []
    assert policy.calls == [("knowledge.original.read", "knowledge-original", "krv_001")]


def test_original_image_is_authorized_and_private():
    http, spy = client()

    response = http.get(BASE + "/items/pi_001/original-image")

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert spy.calls == [("original-image", "krv_001", "pi_001")]


def test_original_image_permission_denial_never_reaches_artifact_service():
    policy = DenyPolicy()
    http, spy = client(policy)

    response = http.get(BASE + "/items/pi_001/original-image")

    assert response.status_code == 403
    assert spy.calls == []


def test_ready_document_review_is_opened_idempotently_without_client_authority():
    policy = RecordingPolicy()
    http, spy = client(policy)
    path = "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review"

    missing_key = http.post(
        path,
        headers={"Origin": ORIGIN},
        json={"sourceRevisionId": "rev_001"},
    )
    opened = http.post(
        path,
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-001"},
        json={"sourceRevisionId": "rev_001"},
    )
    caller_actor = http.post(
        path,
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-actor"},
        json={"sourceRevisionId": "rev_001", "actorId": "forged-editor"},
    )

    assert missing_key.status_code == 422
    assert opened.status_code == 200
    assert opened.json()["status"] == "checking"
    assert opened.json()["version"] == 1
    assert caller_actor.status_code == 403
    expected_review_id = review_id_for("tapper-demo", ("rev_001",))
    assert spy.calls == [
        ("resolve-open", "doc_001", "rev_001"),
        (
            "open",
            "doc_001",
            "rev_001",
            expected_review_id,
            "open-review-001",
        ),
    ]
    assert ("knowledge.read", "knowledge", "doc_001") in policy.calls
    assert (
        "knowledge.review.edit",
        "knowledge-review",
        expected_review_id,
    ) in policy.calls


def test_open_review_authorization_is_restricted_to_server_derived_review_id():
    expected_review_id = review_id_for("tapper-demo", ("rev_001",))
    policy = ReviewResourcePolicy(expected_review_id)
    http, spy = client(policy)

    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review",
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-resource"},
        json={"sourceRevisionId": "rev_001"},
    )

    assert response.status_code == 200
    assert spy.calls == [
        ("resolve-open", "doc_001", "rev_001"),
        (
            "open",
            "doc_001",
            "rev_001",
            expected_review_id,
            "open-review-resource",
        ),
    ]
    assert policy.calls == [
        ("knowledge.read", "knowledge", "doc_001"),
        ("knowledge.review.edit", "knowledge-review", expected_review_id),
    ]


def test_open_review_authorizes_legacy_actual_review_id_before_mutation():
    legacy_review_id = "krv_legacy_actual"
    policy = ReviewResourcePolicy(legacy_review_id)
    http, spy = client(policy)
    spy.open_target_review_id = legacy_review_id

    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review",
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-legacy"},
        json={"sourceRevisionId": "rev_001"},
    )

    assert response.status_code == 200
    assert response.json()["reviewId"] == legacy_review_id
    assert spy.calls[-1] == (
        "open",
        "doc_001",
        "rev_001",
        legacy_review_id,
        "open-review-legacy",
    )
    assert policy.calls[-1] == (
        "knowledge.review.edit",
        "knowledge-review",
        legacy_review_id,
    )


def test_open_review_denied_for_actual_legacy_id_never_mutates_or_returns_detail():
    derived_review_id = review_id_for("tapper-demo", ("rev_001",))
    policy = ReviewResourcePolicy(derived_review_id)
    http, spy = client(policy)
    spy.open_target_review_id = "krv_legacy_denied"

    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review",
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-legacy-denied"},
        json={"sourceRevisionId": "rev_001"},
    )

    assert response.status_code == 403
    assert "reviewId" not in response.json()
    assert spy.calls == [("resolve-open", "doc_001", "rev_001")]
    assert policy.calls[-1] == (
        "knowledge.review.edit",
        "knowledge-review",
        "krv_legacy_denied",
    )


def test_document_open_rejects_multi_source_target_before_review_authorization_or_detail():
    derived_review_id = review_id_for("tapper-demo", ("rev_001",))
    policy = ReviewResourcePolicy(derived_review_id)
    http, spy = client(policy)
    spy.open_target_conflict = "review-source-set-conflict"

    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review",
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-multi-source"},
        json={"sourceRevisionId": "rev_001"},
    )

    assert response.status_code == 409
    assert "reviewId" not in response.json()
    assert spy.calls == [("resolve-open", "doc_001", "rev_001")]
    assert policy.calls == [("knowledge.read", "knowledge", "doc_001")]


def test_open_review_authorization_denial_does_not_reach_the_service():
    policy = DenyPolicy()
    http, spy = client(policy)

    response = http.post(
        "/api/v1/projects/tapper-demo/knowledge/documents/doc_001/review",
        headers={"Origin": ORIGIN, "Idempotency-Key": "open-review-denied"},
        json={"sourceRevisionId": "rev_001"},
    )

    assert response.status_code == 403
    assert spy.calls == []
    assert policy.calls == [("knowledge.read", "knowledge", "doc_001")]


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


def test_review_child_collections_expose_consumable_stable_cursors_and_totals():
    http, spy = client()

    inventory = http.get(BASE + "/inventory", params={"limit": 1, "afterItemId": "pi_000"})
    decisions = http.get(BASE + "/decision-history", params={"limit": 25, "afterVersion": 500})
    history = http.get(BASE + "/history", params={"limit": 20, "afterVersion": 499})
    publications = http.get(
        BASE + "/publications",
        params={"limit": 10, "afterPublicationId": "kpb_000"},
    )

    assert inventory.status_code == decisions.status_code == history.status_code == 200
    assert publications.status_code == 200
    assert inventory.json()["totalCount"] == 2
    assert inventory.json()["nextCursor"] == "pi_001"
    assert decisions.json() == {"items": [], "totalCount": 503, "nextCursor": 501}
    assert history.json() == {"items": [], "totalCount": 507, "nextCursor": 500}
    assert publications.json()["totalCount"] == 502
    assert publications.json()["nextCursor"] == "kpb_001"
    assert spy.calls == [
        ("inventory", "krv_001", 1, "pi_000"),
        ("decision-history", "krv_001", 25, 500),
        ("history", "krv_001", 20, 499),
        ("publications", "krv_001", 10, "kpb_000"),
    ]


def test_review_read_model_survives_refresh_and_exposes_partial_failure_and_capabilities():
    http, spy = client()

    listed = http.get(
        "/api/v1/projects/tapper-demo/knowledge/reviews",
        params={
            "sourceRevisionId": "rev_001",
            "limit": 25,
            "afterReviewId": "krv_000",
        },
    )
    refreshed = http.get(BASE)
    comparison = http.get(BASE + "/items/pi_failed/comparison")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["inventory"] == {
        "items": listed.json()["items"][0]["inventory"]["items"],
        "totalCount": 2,
        "parsedCount": 1,
        "failedCount": 1,
        "needsReviewCount": 0,
        "excludedCount": 0,
        "nextCursor": None,
    }
    assert refreshed.status_code == 200
    assert refreshed.json()["publicationIds"] == ["kpb_historical", "kpb_001"]
    assert refreshed.json()["allowedActions"] == ["edit", "submit", "read_original"]
    assert refreshed.json()["publicationTarget"]["generation"] == "generation-001"
    assert comparison.status_code == 200
    assert comparison.json()["original"]["availability"] == "unsupported"
    assert comparison.json()["extracted"]["availability"] == "unavailable"
    assert spy.calls == [
        ("list", "rev_001", 25, "krv_000"),
        ("get", "krv_001"),
        ("compare", "krv_001", "pi_failed"),
    ]


def test_review_contract_bounds_embedded_collections_and_exposes_page_shapes():
    schemas = create_app().openapi()["components"]["schemas"]
    detail = schemas["KnowledgeReviewDetail"]["properties"]
    inventory = schemas["KnowledgeReviewInventory"]["properties"]

    assert inventory["items"]["maxItems"] == 100
    assert detail["decisions"]["maxItems"] == 500
    assert detail["decisionHistory"]["maxItems"] == 100
    assert detail["history"]["maxItems"] == 100
    assert detail["publicationIds"]["maxItems"] == 100


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


def test_flowchart_correction_is_authorized_and_versioned():
    policy = RecordingPolicy()
    http, spy = client(policy)
    graph = {
        "nodes": [{"id": "a", "label": "开始", "lane": "", "box": [0, 0, 10, 10]}],
        "edges": [],
    }
    response = http.get(BASE + "/flowchart")
    assert response.status_code == 200
    assert response.json() == graph
    saved = http.put(BASE + "/flowchart", json=graph, headers={"Origin": ORIGIN, "If-Match": '"3"'})
    assert saved.status_code == 200
    assert saved.json() == {"sourceRevisionId": "rev_corrected"}
    assert spy.calls[-1] == ("correct-flowchart", "krv_001", graph, 3)
    assert ("knowledge.review.edit", "knowledge-review", "krv_001") in policy.calls
    assert ("knowledge.original.read", "knowledge-original", "krv_001") in policy.calls
    malformed = http.put(
        BASE + "/flowchart",
        json={**graph, "nodes": [{**graph["nodes"][0], "box": [0, 0, True, 10]}]},
        headers={"Origin": ORIGIN, "If-Match": '"3"'},
    )
    assert malformed.status_code == 422


def test_flowchart_correction_denial_never_reaches_service():
    http, spy = client(DenyPolicy())
    graph = {
        "nodes": [{"id": "a", "label": "开始", "lane": "", "box": [0, 0, 10, 10]}],
        "edges": [],
    }
    response = http.put(
        BASE + "/flowchart", json=graph, headers={"Origin": ORIGIN, "If-Match": '"3"'}
    )
    assert response.status_code == 403
    assert spy.calls == []


def test_flowchart_graph_validation_failure_is_not_a_version_conflict():
    http, spy = client()

    async def reject(*args):
        raise ReviewStateConflict("invalid-flowchart-correction")

    spy.correct_flowchart = reject
    response = http.put(
        BASE + "/flowchart",
        json={"nodes": [{"id": "a", "label": "a", "lane": "", "box": [0, 0, 10, 10]}], "edges": []},
        headers={"Origin": ORIGIN, "If-Match": '"3"'},
    )
    assert response.status_code == 422
    assert response.json()["type"].endswith("/request-validation")


def test_original_download_requires_original_read_permission():
    denied, spy = client(DenyPolicy())
    response = denied.get(BASE + "/items/pi_001/original")
    assert response.status_code == 403
    assert not spy.calls
    policy = RecordingPolicy()
    allowed, spy = client(policy)
    response = allowed.get(BASE + "/items/pi_001/original")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 source"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == "attachment"
    assert ("knowledge.original.read", "knowledge-original", "krv_001") in policy.calls
