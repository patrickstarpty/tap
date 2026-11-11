"""Authorized debug-flow orchestration, with durable intent before enqueue.

Only ``debug_run`` and ``run_release`` admit execution. Retries reconcile the original immutable
request, never the current draft, and never rerun an unknown execution. This is
an internal service boundary; gateway authentication remains the caller's job.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.service import BrowserService, digest

from .definition import (
    SCHEMA_VERSION,
    canonical_bytes,
    resolve_parameters,
    validate_definition,
)
from .generator import GENERATOR_VERSION, GeneratedPlaywright, generate_playwright
from .store import LcaConflict, LcaStore


class LcaService:
    def __init__(self, store: LcaStore, browser: BrowserService) -> None:
        self.store = store
        self.browser = browser

    @staticmethod
    def _authorize(principal: AccessPrincipal, action: str) -> tuple[str, str]:
        if (
            not isinstance(principal, AccessPrincipal)
            or principal.principal_type != "user"
            or not principal.enabled
            or principal.expires_at <= datetime.now(UTC)
            or principal.audience != "tap-lca"
            or action not in principal.actions
            or len(principal.principal_id) > 80
        ):
            raise PermissionError("LCA action forbidden")
        return principal.project_id, principal.principal_id

    @staticmethod
    def _browser_principal(principal: AccessPrincipal) -> AccessPrincipal:
        # Constructed only after owner/action authorization, never supplied by UI.
        return AccessPrincipal(
            project_id=principal.project_id,
            principal_id="lca-debug-service",
            principal_type="service",
            audience="tap-browser",
            expires_at=principal.expires_at,
            actions=frozenset(
                {
                    "browser.bundles.create",
                    "browser.executions.create",
                    "browser.executions.read",
                    "browser.executions.cancel",
                    "browser.evidence.read",
                }
            ),
            enabled=True,
        )

    def create_flow(
        self,
        principal: AccessPrincipal,
        key: str,
        title: str,
        definition: object,
        provenance: Any = None,
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.flows.create")
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise ValueError("Flow title must be nonempty and bounded")
        return self.store.create_flow(
            project,
            actor,
            key,
            title,
            canonical_bytes(validate_definition(definition)),
            provenance,
        )

    def get_flow(self, principal: AccessPrincipal, flow_id: str) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.flows.read")
        return self.store.get_flow(project, actor, flow_id)

    def list_flows(self, principal: AccessPrincipal) -> list[dict[str, Any]]:
        project, actor = self._authorize(principal, "lca.flows.read")
        return self.store.list_flows(project, actor)

    def save_draft(
        self,
        principal: AccessPrincipal,
        flow_id: str,
        expected_version: int,
        definition: object,
        provenance: Any = None,
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.drafts.save")
        self.store.get_flow(project, actor, flow_id)
        self._version(expected_version)
        return self.store.save_draft(
            project,
            actor,
            flow_id,
            expected_version,
            canonical_bytes(validate_definition(definition)),
            provenance,
        )

    def generate_draft(
        self,
        principal: AccessPrincipal,
        flow_id: str,
        parameters: Mapping[str, object] | None = None,
    ) -> GeneratedPlaywright:
        project, actor = self._authorize(principal, "lca.drafts.generate")
        flow = self.store.get_flow(project, actor, flow_id)
        return generate_playwright(
            validate_definition(json.loads(flow["canonical_bytes"])), parameters
        )

    @staticmethod
    def _version(version: int) -> None:
        if type(version) is not int or version < 1:
            raise ValueError("Expected draft version must be a positive integer")

    def debug_run(
        self,
        principal: AccessPrincipal,
        flow_id: str,
        expected_version: int,
        parameters: Mapping[str, object] | None,
        key: str,
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.runs.create")
        self._version(expected_version)
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("Invalid idempotency key")
        identity = digest(
            {
                "flow_id": flow_id,
                "version": expected_version,
                "parameters": dict(parameters or {}),
            }
        )
        prior = self.store.get_run_by_key(project, actor, key)
        if prior is not None:
            if prior["execution_request"]["request_identity"] != identity:
                raise LcaConflict("idempotency conflict")
            return self._reconcile(principal, prior)
        flow = self.store.get_flow(project, actor, flow_id)
        if flow["version"] != expected_version:
            raise LcaConflict("draft version conflict")
        definition = validate_definition(json.loads(flow["canonical_bytes"]))
        # Validate supplied data before publishing any run intent.
        inputs = resolve_parameters(definition, parameters)
        snapshot = self.store.freeze_snapshot(
            project,
            actor,
            flow_id,
            expected_version,
            SCHEMA_VERSION,
            GENERATOR_VERSION,
            definition.allowed_origins,
        )
        generated = generate_playwright(
            validate_definition(json.loads(snapshot["canonical_bytes"])), inputs
        )
        bundle = self.browser.create_bundle(
            self._browser_principal(principal),
            {
                "task_id": flow_id,
                "script": generated.source,
                "sha256": hashlib.sha256(generated.source.encode()).hexdigest(),
                "input_data": inputs,
                "input_sha256": generated.input_digest,
                "allowed_origins": snapshot["allowed_origins"],
            },
            "lca-bundle-" + digest([actor, key, identity]),
        )
        intent = self.store.create_run(
            project,
            actor,
            key,
            snapshot["id"],
            bundle["bundle_id"],
            generated.input_digest,
            {
                "request_identity": identity,
                "browser_request": {
                    "task_id": flow_id,
                    "bundle_id": bundle["bundle_id"],
                    "input_sha256": generated.input_digest,
                },
            },
        )
        return self._reconcile(principal, intent)

    def publish_release(
        self,
        principal: AccessPrincipal,
        flow_id: str,
        expected_version: int,
        verified_run_id: str,
        key: str,
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.releases.publish")
        self._version(expected_version)
        flow = self.store.get_flow(project, actor, flow_id)
        run = self.get_run(principal, verified_run_id)
        snapshot = self.store.get_snapshot(project, actor, run["snapshot_id"])
        if run["flow_id"] != flow_id or snapshot["version"] != expected_version:
            raise LcaConflict("Verification run must match the published draft version")
        if (
            not run["execution"]
            or run["execution"]["status"] != "passed"
            or run["canonical_results_error"]
            or not run["canonical_results"]
            or any(item["status"] != "passed" for item in run["canonical_results"])
        ):
            raise LcaConflict(
                "Publication requires a verified passing replay of every step"
            )
        return self.store.create_release(
            project, actor, key, flow, snapshot, verified_run_id
        )

    def list_releases(
        self, principal: AccessPrincipal, flow_id: str
    ) -> list[dict[str, Any]]:
        project, actor = self._authorize(principal, "lca.releases.read")
        return self.store.list_releases(project, actor, flow_id)

    def get_release(
        self, principal: AccessPrincipal, release_id: str
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.releases.read")
        release = self.store.get_release(project, actor, release_id)
        snapshot = self.store.get_snapshot(project, actor, release["snapshot_id"])
        return {
            **release,
            "definition": json.loads(snapshot["canonical_bytes"]),
            "generator_version": snapshot["generator_version"],
        }

    def run_release(
        self,
        principal: AccessPrincipal,
        release_id: str,
        parameters: Mapping[str, object] | None,
        key: str,
    ) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.runs.create")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("Invalid idempotency key")
        self._authorize(principal, "lca.releases.read")
        release = self.store.get_release(project, actor, release_id)
        snapshot = self.store.get_snapshot(project, actor, release["snapshot_id"])
        if snapshot["generator_version"] != GENERATOR_VERSION:
            raise LcaConflict(
                "Published generator version unavailable; validate and publish a new revision"
            )
        identity = digest(
            {"release_id": release_id, "parameters": dict(parameters or {})}
        )
        prior = self.store.get_run_by_key(project, actor, key)
        if prior is not None:
            if prior["execution_request"]["request_identity"] != identity:
                raise LcaConflict("idempotency conflict")
            return self._reconcile(principal, prior)
        definition = validate_definition(json.loads(snapshot["canonical_bytes"]))
        inputs = resolve_parameters(definition, parameters)
        generated = generate_playwright(definition, inputs)
        bundle = self.browser.create_bundle(
            self._browser_principal(principal),
            {
                "task_id": release["flow_id"],
                "script": generated.source,
                "sha256": hashlib.sha256(generated.source.encode()).hexdigest(),
                "input_data": inputs,
                "input_sha256": generated.input_digest,
                "allowed_origins": snapshot["allowed_origins"],
            },
            "lca-release-bundle-" + digest([actor, key, identity]),
        )
        intent = self.store.create_run(
            project,
            actor,
            key,
            snapshot["id"],
            bundle["bundle_id"],
            generated.input_digest,
            {
                "request_identity": identity,
                "release_id": release_id,
                "browser_request": {
                    "task_id": release["flow_id"],
                    "bundle_id": bundle["bundle_id"],
                    "input_sha256": generated.input_digest,
                },
            },
            classification="published",
        )
        return self._reconcile(principal, intent)

    def _reconcile(
        self, principal: AccessPrincipal, intent: dict[str, Any]
    ) -> dict[str, Any]:
        if intent["execution_id"] is not None:
            return intent
        body = intent["execution_request"]["browser_request"]
        # Query the execution authority's durable receipt before retrying an
        # enqueue whose response may have been lost. Same key/body also fences
        # concurrent reconcilers when the query finds nothing.
        prior = self.browser.store.existing_idempotent(
            "execution", principal.project_id, intent["execution_key"], digest(body)
        )
        internal = self._browser_principal(principal)
        execution = (
            self.browser.get_execution(internal, prior["id"])
            if prior is not None
            else self.browser.create_execution(internal, body, intent["execution_key"])
        )
        return self.store.link_execution(
            principal.project_id,
            principal.principal_id,
            intent["id"],
            execution["execution_id"],
        )

    def reconcile_run(self, principal: AccessPrincipal, run_id: str) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.runs.create")
        return self._reconcile(principal, self.store.get_run(project, actor, run_id))

    def get_run(self, principal: AccessPrincipal, run_id: str) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.runs.read")
        run = self.store.get_run(project, actor, run_id)
        execution = None
        if run["execution_id"] is not None:
            execution = self.browser.get_execution(
                self._browser_principal(principal), run["execution_id"]
            )
        snapshot = self.store.get_snapshot(project, actor, run["snapshot_id"])
        canonical_results, canonical_error = self._canonical_results(
            snapshot, execution
        )
        return {
            **run,
            "execution": execution,
            "canonical_results": canonical_results,
            "canonical_results_error": canonical_error,
            "definition_snapshot": {
                "version": snapshot["version"],
                "definition": json.loads(snapshot["canonical_bytes"]),
                "digest": snapshot["definition_digest"],
                "generator_version": snapshot["generator_version"],
            },
        }

    def list_runs(
        self, principal: AccessPrincipal, flow_id: str
    ) -> list[dict[str, Any]]:
        project, actor = self._authorize(principal, "lca.runs.read")
        return self.store.list_runs(project, actor, flow_id)

    def cancel_run(self, principal: AccessPrincipal, run_id: str) -> dict[str, Any]:
        project, actor = self._authorize(principal, "lca.runs.cancel")
        run = self.store.get_run(project, actor, run_id)
        if run["execution_id"] is None:
            raise LcaConflict("Run must be reconciled before cancellation")
        return self.browser.cancel_execution(
            self._browser_principal(principal), run["execution_id"]
        )

    def read_evidence(
        self, principal: AccessPrincipal, run_id: str, evidence_id: str
    ) -> tuple[dict[str, Any], bytes]:
        project, actor = self._authorize(principal, "lca.evidence.read")
        run = self.store.get_run(project, actor, run_id)
        row = self.browser.store.get("evidence", project, evidence_id)
        if (
            run["execution_id"] is None
            or row is None
            or row["execution_id"] != run["execution_id"]
            or row["task_id"] != run["flow_id"]
        ):
            raise PermissionError("LCA evidence unavailable")
        return self.browser.read_evidence(
            self._browser_principal(principal), evidence_id
        )

    @staticmethod
    def _canonical_results(
        snapshot: dict[str, Any], execution: dict[str, Any] | None
    ) -> tuple[list[dict[str, str]] | None, str | None]:
        if execution is None:
            return None, "Execution not linked"
        result = execution.get("result")
        outputs = result.get("outputs") if isinstance(result, dict) else None
        if not isinstance(outputs, list):
            return None, "Canonical results unavailable"
        candidates = [
            item
            for item in outputs
            if isinstance(item, dict)
            and item.get("schema_version") == "lca-debug-result-v1"
        ]
        if len(candidates) != 1:
            return None, "Canonical result attachment missing or ambiguous"
        candidate = candidates[0]
        steps = candidate.get("steps")
        definition = json.loads(snapshot["canonical_bytes"])
        expected = [step["id"] for step in definition["steps"]]
        if (
            candidate.get("definition_digest") != snapshot["definition_digest"]
            or not isinstance(steps, list)
            or len(steps) != len(expected)
        ):
            return None, "Canonical result does not match frozen definition"
        validated = []
        for step_id, step in zip(expected, steps, strict=True):
            if (
                not isinstance(step, dict)
                or set(step) != {"step_id", "status"}
                or step.get("step_id") != step_id
                or not isinstance(step.get("status"), str)
                or step.get("status") not in {"passed", "failed", "not_run"}
            ):
                return None, "Canonical step results are invalid"
            validated.append({"step_id": step_id, "status": step["status"]})
        return validated, None
