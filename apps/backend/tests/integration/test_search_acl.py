"""Opt-in real Azure AI Search authorization contract.

This test is skipped during ordinary local/CI runs. Setting the gate to ``1`` is
an explicit claim that a non-production, sanitized fixture index is available;
missing configuration then fails instead of being reported as a skip.
"""

from __future__ import annotations

import hashlib
import json
import math
import os

import pytest

from tap.modules.access.application.authorize import build_retrieval_policy_context
from tap.modules.access.domain.policy import (
    Classification,
    ProjectPolicy,
    VerifiedSubjectFacts,
)
from tap.modules.knowledge.adapters.azure_ai_search import (
    AzureAISearchAdapter,
    AzureIndexTarget,
    AzureSearchConfig,
)
from tap.modules.knowledge.domain.models import (
    AnswerMode,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    QueryPlan,
    RetrievalProfileId,
    SourceFamily,
)
from tap.modules.knowledge.ports.models import SearchExecution

RUN_GATE = "TAP_RUN_AZURE_INTEGRATION"
REQUIRED_ENVIRONMENT = (
    "TAP_AZURE_SEARCH_ENDPOINT",
    "TAP_AZURE_SEARCH_API_KEY",
    "TAP_AZURE_SEARCH_INDEX",
    "TAP_AZURE_SEARCH_PHYSICAL_INDEX",
    "TAP_AZURE_SEARCH_SCHEMA_VERSION",
    "TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID",
    "TAP_AZURE_SEARCH_VECTOR_DIMENSION",
    "TAP_AZURE_TEST_TENANT_ID",
    "TAP_AZURE_TEST_PROJECT_ID",
    "TAP_AZURE_TEST_ALLOWED_GROUP_ID",
    "TAP_AZURE_TEST_DENIED_GROUP_ID",
    "TAP_AZURE_TEST_CLASSIFICATION_CEILING",
    "TAP_AZURE_TEST_ENVIRONMENT",
    "TAP_AZURE_TEST_CORPUS_VERSION",
    "TAP_AZURE_TEST_EXPECTED_SOURCE_ID",
    "TAP_AZURE_TEST_QUERY_VECTOR_JSON",
    "TAP_AZURE_TEST_DATASET_MARKER",
)


def required_configuration() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.getenv(name)]
    if missing:
        pytest.fail(
            "Azure integration gate is enabled but configuration is missing: " + ", ".join(missing)
        )
    values = {name: os.environ[name] for name in REQUIRED_ENVIRONMENT}
    if values["TAP_AZURE_TEST_DATASET_MARKER"] != "non-production-sanitized":
        pytest.fail(
            "TAP_AZURE_TEST_DATASET_MARKER must explicitly declare non-production-sanitized"
        )
    if values["TAP_AZURE_TEST_ALLOWED_GROUP_ID"] == values["TAP_AZURE_TEST_DENIED_GROUP_ID"]:
        pytest.fail("Azure ACL fixture requires distinct allowed and denied group IDs")
    try:
        dimension = int(values["TAP_AZURE_SEARCH_VECTOR_DIMENSION"])
    except ValueError:
        pytest.fail("TAP_AZURE_SEARCH_VECTOR_DIMENSION must be a strict positive integer")
    if str(dimension) != values["TAP_AZURE_SEARCH_VECTOR_DIMENSION"] or not 1 <= dimension <= 4_096:
        pytest.fail("TAP_AZURE_SEARCH_VECTOR_DIMENSION must be a strict positive integer")
    try:
        vector = json.loads(values["TAP_AZURE_TEST_QUERY_VECTOR_JSON"])
    except json.JSONDecodeError:
        pytest.fail("TAP_AZURE_TEST_QUERY_VECTOR_JSON must be a finite JSON number array")
    if (
        not isinstance(vector, list)
        or len(vector) != dimension
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        )
    ):
        pytest.fail(
            "TAP_AZURE_TEST_QUERY_VECTOR_JSON must match the configured finite vector space"
        )
    return values


def policy_for(config: dict[str, str], group_id: str):
    classification = Classification(config["TAP_AZURE_TEST_CLASSIFICATION_CEILING"])
    subject = VerifiedSubjectFacts(
        tenant_id=config["TAP_AZURE_TEST_TENANT_ID"],
        user_id="tap-integration-probe",
        group_ids=frozenset({group_id}),
        roles=frozenset({"reader"}),
        token_verified=True,
    )
    project = ProjectPolicy(
        tenant_id=config["TAP_AZURE_TEST_TENANT_ID"],
        project_id=config["TAP_AZURE_TEST_PROJECT_ID"],
        permission_granted=True,
        allowed_group_ids=frozenset({group_id}),
        classification_ceiling=classification,
        allowed_environments=frozenset({config["TAP_AZURE_TEST_ENVIRONMENT"]}),
        allowed_source_families=frozenset({"doc"}),
        active_corpus_version=config["TAP_AZURE_TEST_CORPUS_VERSION"],
        acl_digest="integration-probe",
        policy_version="integration-probe",
        decision_id=f"integration-probe:{group_id}",
    )
    return build_retrieval_policy_context(
        subject,
        project,
        requested_tenant_id=config["TAP_AZURE_TEST_TENANT_ID"],
        requested_project_id=config["TAP_AZURE_TEST_PROJECT_ID"],
    )


def execution(config: dict[str, str], group_id: str) -> SearchExecution:
    policy = policy_for(config, group_id)
    sanitized_query = "sanitized authorization fixture"
    query_hash = "sha256:" + hashlib.sha256(sanitized_query.encode("utf-8")).hexdigest()
    plan = QueryPlan(
        query_plan_id=f"azure-acl-plan:{group_id}",
        operation_id=f"azure-acl-operation:{group_id}",
        tenant_id=policy.tenant_id,
        project_id=policy.project_id,
        policy_decision_id=policy.decision_id,
        policy_version=policy.policy_version,
        acl_digest=policy.acl_digest,
        answer_mode=AnswerMode.QUICK,
        retrieval_profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        source_families=(SourceFamily.DOC,),
        resources=(),
        effective_environment=config["TAP_AZURE_TEST_ENVIRONMENT"],
        corpus_version=config["TAP_AZURE_TEST_CORPUS_VERSION"],
        candidate_limit=10,
        raw_request_hash="sha256:" + hashlib.sha256(b"azure-acl-probe").hexdigest(),
        sanitized_query=sanitized_query,
        sanitized_query_hash=query_hash,
        redaction_version="integration-sanitized-v1",
        embedding_model_id=config["TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID"],
        embedding_dimension=int(config["TAP_AZURE_SEARCH_VECTOR_DIMENSION"]),
    )
    return SearchExecution(
        policy=policy,
        plan=plan,
        context_snapshot=ContextSnapshot(
            context_snapshot_id=f"azure-acl-snapshot:{group_id}",
            operation_id=plan.operation_id,
            tenant_id=policy.tenant_id,
            project_id=policy.project_id,
            policy_decision_id=policy.decision_id,
            policy_version=policy.policy_version,
            acl_digest=policy.acl_digest,
            layers=(
                ContextLayer(
                    kind=ContextLayerKind.CURRENT_TURN,
                    ref_ids=(),
                    content_hash=query_hash,
                    token_count=3,
                ),
            ),
        ),
        query_vector=tuple(
            float(value) for value in json.loads(config["TAP_AZURE_TEST_QUERY_VECTOR_JSON"])
        ),
    )


@pytest.mark.asyncio
async def test_real_azure_search_returns_zero_unauthorized_hits() -> None:
    """The denied group must receive zero hits against a proven, visible fixture."""
    if os.getenv(RUN_GATE) != "1":
        pytest.skip(f"set {RUN_GATE}=1 with a sanitized Azure fixture to run the real ACL gate")

    config = required_configuration()
    adapter = AzureAISearchAdapter(
        AzureSearchConfig(
            endpoint=config["TAP_AZURE_SEARCH_ENDPOINT"],
            indexes={
                SourceFamily.DOC: AzureIndexTarget(
                    query_index=config["TAP_AZURE_SEARCH_INDEX"],
                    physical_index=config["TAP_AZURE_SEARCH_PHYSICAL_INDEX"],
                    schema_version=config["TAP_AZURE_SEARCH_SCHEMA_VERSION"],
                    embedding_model_id=config["TAP_AZURE_SEARCH_EMBEDDING_MODEL_ID"],
                    vector_dimension=int(config["TAP_AZURE_SEARCH_VECTOR_DIMENSION"]),
                )
            },
            query_api_key=config["TAP_AZURE_SEARCH_API_KEY"],
            allow_query_key_auth=True,
            max_fan_out=1,
            per_index_candidates=10,
            max_connections=1,
            deadline_seconds=10,
            max_retries=1,
        )
    )
    try:
        authorized = await adapter.search(
            execution(config, config["TAP_AZURE_TEST_ALLOWED_GROUP_ID"])
        )
        assert config["TAP_AZURE_TEST_EXPECTED_SOURCE_ID"] in {
            hit.source.source_id for hit in authorized
        }, "sanitized positive-control fixture was not visible"

        unauthorized = await adapter.search(
            execution(config, config["TAP_AZURE_TEST_DENIED_GROUP_ID"])
        )
        assert unauthorized == ()
    finally:
        await adapter.close()
