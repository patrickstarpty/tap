"""Persistent search attempts store bounded facts, never text or provider context."""


def test_search_audit_authoritative_inventory_is_closed():
    from tap.platform.db.registry import load_authoritative_metadata

    table = load_authoritative_metadata().tables["knowledge_search_audit"]
    assert {
        "query_hash",
        "policy_digest",
        "policy_version",
        "family",
        "provider_candidate_count",
        "mapped_candidate_count",
    } <= set(table.c.keys())
    assert not {"query", "evidence", "provider_request_ids", "physical_collection"} & set(
        table.c.keys()
    )


def test_search_audit_persists_only_bound_candidate_facts(owned_project_mysql):
    import asyncio
    from dataclasses import replace

    import pytest
    from apps.backend.tests.owned_mysql import owned_project_database_url
    from sqlalchemy import select

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.knowledge.adapters.milvus.audit import (
        MilvusSearchAuditEvent,
        SearchAuditMetadata,
    )
    from tap.modules.knowledge.adapters.mysql_audit import (
        MysqlSearchAuditSink,
        knowledge_search_audit,
    )
    from tap.platform.db.session import create_engine_and_session_factory

    async def run():
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        facts = SearchAuditMetadata(
            "local",
            "tapper-demo",
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
            "tapper-demo-policy-v1",
            "tapper-pattern-egress-v1",
            10,
        )
        event = MilvusSearchAuditEvent(
            outcome="success",
            provider="milvus",
            query_plan_id="opaque-plan",
            acl_digest=facts.policy_digest,
            alias="private-provider-context",
            physical_collection="private-provider-context",
            schema_version="schema",
            corpus_version="corpus",
            embedding_model_version="embedding",
            provider_row_count=3,
            rejected_row_count=0,
            elapsed_milliseconds=1,
            provider_request_ids=("private-provider-context",),
            error_code=None,
            metadata=facts,
        )
        sink = MysqlSearchAuditSink(
            sessions, scope=VALIDATION_SCOPE, policy_version="tapper-demo-policy-v1"
        )
        try:
            await sink.emit(event)
            await sink.emit(
                replace(event, outcome="failure", error_code="unavailable", metadata=None)
            )
            with pytest.raises(ValueError):
                await sink.emit(replace(event, metadata=replace(facts, project_id="other-project")))
            async with sessions() as session:
                rows = (await session.execute(select(knowledge_search_audit))).mappings().all()
                assert len(rows) == 2
                assert all("private-provider-context" not in str(row) for row in rows)
                success = next(row for row in rows if row["outcome"] == "success")
                assert success["mapped_candidate_count"] == success["provider_candidate_count"] == 3
                assert success["query_hash"] == facts.query_hash
        finally:
            await engine.dispose()

    asyncio.run(run())
