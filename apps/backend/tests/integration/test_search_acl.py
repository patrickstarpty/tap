"""Opt-in real Azure AI Search authorization contract.

This test is skipped during ordinary local/CI runs. Setting the gate to ``1`` is
an explicit claim that a non-production, sanitized fixture index is available;
missing configuration then fails instead of being reported as a skip.
"""

from __future__ import annotations

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
    AzureSearchConfig,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.models import SearchExecution

RUN_GATE = "TAP_RUN_AZURE_INTEGRATION"
REQUIRED_ENVIRONMENT = (
    "TAP_AZURE_SEARCH_ENDPOINT",
    "TAP_AZURE_SEARCH_API_KEY",
    "TAP_AZURE_SEARCH_INDEX",
    "TAP_AZURE_TEST_TENANT_ID",
    "TAP_AZURE_TEST_PROJECT_ID",
    "TAP_AZURE_TEST_ALLOWED_GROUP_ID",
    "TAP_AZURE_TEST_DENIED_GROUP_ID",
    "TAP_AZURE_TEST_CLASSIFICATION_CEILING",
    "TAP_AZURE_TEST_ENVIRONMENT",
    "TAP_AZURE_TEST_CORPUS_VERSION",
    "TAP_AZURE_TEST_EXPECTED_SOURCE_ID",
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
    return SearchExecution(
        query="*",
        query_vector=(),
        source_families=(SourceFamily.DOC,),
        resources=(),
        effective_environment=config["TAP_AZURE_TEST_ENVIRONMENT"],
        corpus_version=config["TAP_AZURE_TEST_CORPUS_VERSION"],
        candidate_limit=10,
        profile_id="azure-acl-integration-v1",
        policy=policy_for(config, group_id),
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
            api_key=config["TAP_AZURE_SEARCH_API_KEY"],
            index_aliases={SourceFamily.DOC: config["TAP_AZURE_SEARCH_INDEX"]},
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
