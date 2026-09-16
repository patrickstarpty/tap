"""Opt-in real current Entra/Project-Policy revocation contract.

Ordinary runs skip this external gate. Setting the gate to ``1`` asserts that
two non-production sanitized policy probes exist; missing settings then fail.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

RUN_GATE = "TAP_RUN_ENTRA_POLICY_INTEGRATION"
REQUIRED_ENVIRONMENT = (
    "TAP_POLICY_TEST_ACTIVE_URL",
    "TAP_POLICY_TEST_REVOKED_URL",
    "TAP_POLICY_TEST_BEARER_TOKEN",
    "TAP_POLICY_TEST_TENANT_ID",
    "TAP_POLICY_TEST_PROJECT_ID",
    "TAP_POLICY_TEST_USER_ID",
    "TAP_POLICY_TEST_ACTIVE_DECISION_ID",
    "TAP_POLICY_TEST_DATASET_MARKER",
)
MAX_POLICY_RESPONSE_BYTES = 65_536


def required_configuration() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.getenv(name)]
    if missing:
        pytest.fail(
            "Entra/Project-Policy integration gate is enabled but configuration is missing: "
            + ", ".join(missing)
        )
    values = {name: os.environ[name] for name in REQUIRED_ENVIRONMENT}
    if values["TAP_POLICY_TEST_DATASET_MARKER"] != "non-production-sanitized":
        pytest.fail(
            "TAP_POLICY_TEST_DATASET_MARKER must explicitly declare non-production-sanitized"
        )
    for name in ("TAP_POLICY_TEST_ACTIVE_URL", "TAP_POLICY_TEST_REVOKED_URL"):
        if not values[name].startswith("https://"):
            pytest.fail(f"{name} must use HTTPS")
    if values["TAP_POLICY_TEST_ACTIVE_URL"] == values["TAP_POLICY_TEST_REVOKED_URL"]:
        pytest.fail("active and revoked policy probes must be distinct")
    if len(values["TAP_POLICY_TEST_BEARER_TOKEN"]) > 16_384:
        pytest.fail("policy probe bearer token exceeds the bounded credential size")
    return values


@pytest.mark.asyncio
async def test_real_current_policy_probe_observes_active_then_revoked_permission() -> None:
    """A sanitized current-policy fixture must distinguish active and revoked access."""
    if os.getenv(RUN_GATE) != "1":
        pytest.skip(f"set {RUN_GATE}=1 with sanitized current-policy probes to run the real gate")

    config = required_configuration()
    headers = {
        "authorization": f"Bearer {config['TAP_POLICY_TEST_BEARER_TOKEN']}",
        "accept": "application/json",
        "x-tap-policy-probe": "current-policy-revocation-v1",
    }
    timeout = httpx.Timeout(connect=3, read=5, write=3, pool=3)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        active_response = await client.get(config["TAP_POLICY_TEST_ACTIVE_URL"], headers=headers)
        assert active_response.status_code == 200
        active = _bounded_json(active_response)
        _assert_fixture_identity(active, config)
        assert active.get("permissionGranted") is True
        assert active.get("decisionId") == config["TAP_POLICY_TEST_ACTIVE_DECISION_ID"]
        _bounded_server_identifier(active, "policyVersion")
        _bounded_server_identifier(active, "aclDigest")

        revoked_response = await client.get(config["TAP_POLICY_TEST_REVOKED_URL"], headers=headers)

    if revoked_response.status_code in {403, 404}:
        return
    assert revoked_response.status_code == 200
    revoked = _bounded_json(revoked_response)
    _assert_fixture_identity(revoked, config)
    assert revoked.get("permissionGranted") is False


def _bounded_json(response: httpx.Response) -> dict[str, Any]:
    if len(response.content) > MAX_POLICY_RESPONSE_BYTES:
        pytest.fail("current-policy probe response exceeds the byte bound")
    try:
        value = json.loads(response.content, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pytest.fail("current-policy probe returned malformed JSON")
    if not isinstance(value, dict):
        pytest.fail("current-policy probe must return a JSON object")
    return value


def _assert_fixture_identity(value: dict[str, Any], config: dict[str, str]) -> None:
    assert value.get("tenantId") == config["TAP_POLICY_TEST_TENANT_ID"]
    assert value.get("projectId") == config["TAP_POLICY_TEST_PROJECT_ID"]
    assert value.get("userId") == config["TAP_POLICY_TEST_USER_ID"]


def _bounded_server_identifier(value: dict[str, Any], field: str) -> None:
    identifier = value.get(field)
    assert isinstance(identifier, str) and 0 < len(identifier) <= 256


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")
