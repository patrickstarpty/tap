from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_conversations import (
    turn_input_snapshot,
)
from tap.platform.db.schema import outbox
from tests.integration.plan_generation_support import (
    generation_request as _request,
)
from tests.integration.plan_generation_support import (
    plan_repository as _repository,
)
from tests.integration.plan_generation_support import (
    seed_completed_turn as _seed_completed_turn,
)
from tests.integration.plan_generation_support import (
    seed_test_design_authority as _seed_test_design_authority,
)
from tests.integration.test_knowledge_publication import (
    NOW,
)


@pytest.mark.asyncio
async def test_generation_job_binds_exact_completed_turn_snapshots_and_replays(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        request = _request(publication)

        first = await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 1)
        )
        replay = await repository.request_generation(
            VALIDATION_SCOPE, request, now=datetime(2026, 9, 13, 12, 2)
        )

        assert replay == first
        assert first.request == request
        assert first.status.value == "PENDING"
        async with sessions() as session:
            event = (
                (
                    await session.execute(
                        select(outbox).where(
                            outbox.c.message_type == "test-plan.generation.requested"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert event["envelope"]["payload"] == {
            "revisionId": request.revision_id,
            "inputSnapshotDigest": request.input_snapshot_digest,
            "answerEvidenceSnapshotDigest": request.answer_evidence_snapshot_digest,
            "requestDigest": request.request_digest,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"input_snapshot_digest": "sha256:" + "9" * 64},
        {"answer_evidence_snapshot_digest": "sha256:" + "9" * 64},
        {"turn_id": "turn_other"},
        {"conversation_id": "conversation_other"},
        {"model_alias": "other-model"},
        {"agent_revision_id": "other-agent"},
        {"skill_revision_ids": ("other-skill",)},
    ],
)
async def test_generation_job_rejects_tampered_or_cross_turn_snapshots(
    owned_project_mysql, changes
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        repository = _repository(sessions)
        with pytest.raises(ValueError, match="snapshot|Turn|governance|model route|model alias"):
            await repository.request_generation(
                VALIDATION_SCOPE,
                _request(publication, **changes),
                now=datetime(2026, 9, 13, 12, 1),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generation_from_turn_rejects_non_design_capable_model_alias(
    owned_project_mysql,
) -> None:
    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        async with sessions() as session, session.begin():
            await session.execute(
                update(turn_input_snapshot)
                .where(turn_input_snapshot.c.turn_id == "turn_checkout")
                .values(
                    snapshot={
                        "model_alias": "qwen3-vl-plus",
                        "agent_revision_id": "validation-knowledge-agent-v2",
                        "skill_revision_ids": ["validation-citation-skill-v2"],
                    }
                )
            )

        with pytest.raises(ValueError, match="design-capable"):
            await _repository(sessions).request_generation_from_turn(
                VALIDATION_SCOPE,
                conversation_id="conversation_checkout",
                turn_id="turn_checkout",
                objective="Design checkout tests",
                idempotency_key="reject-non-design-test-design",
                now=datetime(2026, 9, 13, 12, 1),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generation_scope_combines_current_publications_without_cross_authorizing_items(
    owned_project_mysql,
) -> None:
    from datetime import timedelta

    from tap.modules.knowledge.adapters.mysql_documents import (
        knowledge_document,
        knowledge_document_revision,
        knowledge_parse_inventory,
    )
    from tap.modules.knowledge.adapters.mysql_review import (
        knowledge_current_publication,
        knowledge_publication,
    )

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        publication = await _seed_test_design_authority(sessions)
        await _seed_completed_turn(sessions, source_revision_id=publication.source_revision_ids[0])
        async with sessions() as session, session.begin():
            for table, changes in (
                (
                    knowledge_document,
                    {
                        "document_id": "doc_second",
                        "current_revision_id": None,
                        "dedupe_key": "sha256:" + "f" * 64,
                    },
                ),
                (
                    knowledge_document_revision,
                    {"revision_id": "rev_second", "document_id": "doc_second"},
                ),
                (
                    knowledge_parse_inventory,
                    {
                        "inventory_row_id": "inventory_second",
                        "source_revision_id": "rev_second",
                        "item_id": "item_second",
                    },
                ),
                (
                    knowledge_publication,
                    {
                        "publication_id": "pub_second",
                        "source_revision_ids": ["rev_second"],
                        "approved_item_ids": ["item_second"],
                    },
                ),
                (
                    knowledge_current_publication,
                    {"pointer_id": "pointer_second", "publication_id": "pub_second"},
                ),
            ):
                row = dict((await session.execute(select(table))).mappings().one())
                row.update(changes)
                await session.execute(insert(table).values(**row))
            first_item = dict(
                (
                    await session.execute(
                        select(knowledge_parse_inventory).where(
                            knowledge_parse_inventory.c.source_revision_id
                            == publication.source_revision_ids[0]
                        )
                    )
                )
                .mappings()
                .one()
            )
            first_item.update(
                inventory_row_id="inventory_crossed", item_id="item_second", ordinal=1
            )
            await session.execute(insert(knowledge_parse_inventory).values(**first_item))
            original = (await session.execute(select(turn_input_snapshot.c.snapshot))).scalar_one()
            await session.execute(
                update(turn_input_snapshot).values(
                    snapshot={
                        **original,
                        "resolved_resources": [
                            {"revision_id": revision_id}
                            for revision_id in (*publication.source_revision_ids, "rev_second")
                        ],
                    }
                )
            )
        repository = _repository(sessions)
        job = await repository.request_generation_from_turn(
            VALIDATION_SCOPE,
            conversation_id="conversation_checkout",
            turn_id="turn_checkout",
            objective="Cover both documents",
            idempotency_key="multi-publication-generation",
            now=NOW,
        )
        assert job.request.approved_knowledge_revision_ids == tuple(
            sorted((*publication.source_revision_ids, "rev_second"))
        )
        assert {
            (item.source_revision_id, item.requirement_id)
            for item in job.request.requirement_scope.requirements
        } == {
            (publication.source_revision_ids[0], publication.approved_item_ids[0]),
            ("rev_second", "item_second"),
        }
        async with sessions() as session, session.begin():
            await session.execute(
                update(knowledge_publication)
                .where(knowledge_publication.c.publication_id == "pub_second")
                .values(expires_at=NOW.replace(tzinfo=None) - timedelta(seconds=1))
            )
        with pytest.raises(ValueError, match="current"):
            await repository.request_generation(
                VALIDATION_SCOPE,
                replace(job.request, idempotency_key="expired-multi-publication"),
                now=NOW,
            )
    finally:
        await engine.dispose()
