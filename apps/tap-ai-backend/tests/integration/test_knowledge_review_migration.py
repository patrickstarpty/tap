from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.mysql_review import MysqlKnowledgeReviewRepository
from tap.modules.knowledge.application.review import KnowledgeReviewApplication
from tap.modules.knowledge.domain.review import canonical_digest
from tap.platform.db.session import create_engine_and_session_factory

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 25, 9, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


class UnusedProjection:
    async def verify(self, revision, generation):  # type: ignore[no-untyped-def]
        raise AssertionError("idempotent replay must not verify the projection again")


async def test_0019_publish_and_withdraw_commands_replay_after_0020_upgrade(
    owned_project_mysql,
):
    owned_project_mysql.rebuild("0019_test_design_model_calls")
    sync_engine = create_engine(owned_project_mysql.url)
    published_at = NOW.isoformat()
    expires_at = (NOW + timedelta(days=30)).isoformat()
    withdrawn_at = (NOW + timedelta(minutes=1)).isoformat()
    base_result = {
        "publication_id": "kpb_legacy_001",
        "project_id": VALIDATION_SCOPE.project_id,
        "review_id": "krv_legacy_001",
        "review_version": 4,
        "approval_digest": DIGEST_A,
        "source_revision_ids": ["rev_legacy_001"],
        "approved_item_ids": ["pi_legacy_001"],
        "generation": "generation-legacy",
        "published_by": VALIDATION_SCOPE.actor_id,
        "published_at": published_at,
        "expires_at": expires_at,
    }
    publish_result = base_result | {
        "status": "published",
        "withdrawn_by": None,
        "withdrawn_at": None,
    }
    withdraw_result = base_result | {
        "status": "withdrawn",
        "withdrawn_by": VALIDATION_SCOPE.actor_id,
        "withdrawn_at": withdrawn_at,
    }
    scope = {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO knowledge_review_revision ("
                    "review_id,source_revision_ids,inventory_digest,chunk_manifest_digest,"
                    "annotation_digest,dependency_digest,editor_actor_ids,reviewer_actor_id,"
                    "expires_at,status,version,blocking_item_ids,approved_item_ids,created_at,"
                    "updated_at,enterprise_id,project_id,actor_id,identity_mode,identity_origin"
                    ") VALUES ("
                    "'krv_legacy_001',JSON_ARRAY('rev_legacy_001'),:digest_a,:digest_b,"
                    ":digest_c,:digest_a,JSON_ARRAY('legacy-editor'),'legacy-reviewer',"
                    ":expires_at,'withdrawn',6,JSON_ARRAY(),JSON_ARRAY('pi_legacy_001'),"
                    ":published_at,:withdrawn_at,:enterprise_id,:project_id,:actor_id,"
                    ":identity_mode,:identity_origin)"
                ),
                scope
                | {
                    "digest_a": DIGEST_A,
                    "digest_b": DIGEST_B,
                    "digest_c": DIGEST_C,
                    "published_at": NOW.replace(tzinfo=None),
                    "withdrawn_at": (NOW + timedelta(minutes=1)).replace(tzinfo=None),
                    "expires_at": (NOW + timedelta(days=30)).replace(tzinfo=None),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO knowledge_publication ("
                    "publication_id,review_id,review_version,approval_digest,source_revision_ids,"
                    "approved_item_ids,generation,published_by,published_at,expires_at,status,"
                    "withdrawn_by,withdrawn_at,enterprise_id,project_id,actor_id,identity_mode,"
                    "identity_origin) VALUES ("
                    "'kpb_legacy_001','krv_legacy_001',4,:digest_a,"
                    "JSON_ARRAY('rev_legacy_001'),JSON_ARRAY('pi_legacy_001'),"
                    "'generation-legacy',:actor_id,:published_at,:expires_at,'withdrawn',"
                    ":actor_id,:withdrawn_at,:enterprise_id,:project_id,:actor_id,"
                    ":identity_mode,:identity_origin)"
                ),
                scope
                | {
                    "digest_a": DIGEST_A,
                    "published_at": NOW.replace(tzinfo=None),
                    "withdrawn_at": (NOW + timedelta(minutes=1)).replace(tzinfo=None),
                    "expires_at": (NOW + timedelta(days=30)).replace(tzinfo=None),
                },
            )
            for key, request_digest, result in (
                (
                    "legacy-publish",
                    canonical_digest(
                        {
                            "actorId": VALIDATION_SCOPE.actor_id,
                            "generation": "generation-legacy",
                            "operation": "publish",
                            "reviewId": "krv_legacy_001",
                        }
                    ),
                    publish_result,
                ),
                (
                    "legacy-withdraw",
                    canonical_digest(
                        {
                            "actorId": VALIDATION_SCOPE.actor_id,
                            "operation": "withdraw",
                            "publicationId": "kpb_legacy_001",
                        }
                    ),
                    withdraw_result,
                ),
            ):
                connection.execute(
                    text(
                        "INSERT INTO knowledge_review_command ("
                        "command_id,idempotency_key,request_digest,result,created_at,enterprise_id,"
                        "project_id,actor_id,identity_mode,identity_origin) VALUES ("
                        ":command_id,:key,:request_digest,:result,:created_at,:enterprise_id,"
                        ":project_id,:actor_id,:identity_mode,:identity_origin)"
                    ),
                    scope
                    | {
                        "command_id": "cmd_" + key,
                        "key": key,
                        "request_digest": request_digest,
                        "result": json.dumps(result),
                        "created_at": NOW.replace(tzinfo=None),
                    },
                )
        owned_project_mysql.upgrade("0020_knowledge_review_read_model")
    finally:
        sync_engine.dispose()

    url = owned_project_mysql.url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    engine, sessions = create_engine_and_session_factory(url)
    try:
        application = KnowledgeReviewApplication(
            MysqlKnowledgeReviewRepository(sessions, scope=VALIDATION_SCOPE),
            UnusedProjection(),
        )
        publish_replay = await application.publish_review(
            "krv_legacy_001",
            generation="generation-legacy",
            idempotency_key="legacy-publish",
            actor_id=VALIDATION_SCOPE.actor_id,
            expected_version=4,
            now=NOW,
        )
        withdraw_replay = await application.withdraw_publication(
            "kpb_legacy_001",
            idempotency_key="legacy-withdraw",
            actor_id=VALIDATION_SCOPE.actor_id,
            expected_version=1,
            now=NOW + timedelta(minutes=1),
        )

        assert publish_replay.status == "published" and publish_replay.version == 1
        assert publish_replay.withdrawn_at is None
        assert withdraw_replay.status == "withdrawn" and withdraw_replay.version == 2
        assert withdraw_replay.withdrawn_at == NOW + timedelta(minutes=1)
    finally:
        await engine.dispose()
