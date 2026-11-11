"""Project-bound projection and fetch of browser execution evidence."""

from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import quote

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.ports.browser import BrowserArtifactReader
from tap.modules.workflows.ports.store import WorkflowStore

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_KINDS = {"screenshot", "trace", "result", "video"}


class BrowserArtifactAdapter:
    def __init__(self, store: WorkflowStore, client: BrowserArtifactReader) -> None:
        self.store = store
        self.client = client

    async def project(
        self, scope: ProjectScopeContext, task_id: str, snapshot: dict[str, Any]
    ) -> dict[str, Any] | None:
        if (
            scope.project_id != self.client.project_id
            or snapshot["request_json"].get("kind") != "browser"
        ):
            raise LookupError("browser execution unavailable")
        browser = snapshot["request_json"].get("browser")
        if not isinstance(browser, dict):
            raise LookupError("frozen browser input unavailable")
        result = next(
            (
                event.payload
                for event in reversed(snapshot["events"])
                if event.kind == "browser.coding_result"
                and isinstance(event.payload.get("execution_ref"), str)
                and isinstance(event.payload.get("bundle_ref"), str)
            ),
            None,
        )
        if result is None:
            return None
        execution_id = result["execution_ref"]
        bundle_id = result["bundle_ref"]
        if _ID.fullmatch(execution_id) is None or _ID.fullmatch(bundle_id) is None:
            raise LookupError("browser execution unavailable")
        receipt = await self.client.request("GET", f"/executions/{execution_id}")
        if any(
            receipt.get(name) != expected
            for name, expected in (
                ("project_id", scope.project_id),
                ("task_id", task_id),
                ("execution_id", execution_id),
                ("bundle_id", bundle_id),
                ("input_sha256", browser.get("input_sha256")),
            )
        ):
            raise LookupError("browser execution provenance mismatch")
        steps = receipt.get("steps")
        evidence = receipt.get("evidence")
        result_value = receipt.get("result")
        outputs = result_value.get("outputs", []) if isinstance(result_value, dict) else []
        try:
            output_bytes = json.dumps(
                outputs, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        except (TypeError, ValueError) as error:
            raise LookupError("browser execution result invalid") from error
        if (
            not isinstance(steps, list)
            or not isinstance(evidence, list)
            or not isinstance(outputs, list)
            or len(steps) > 100
            or len(evidence) > 20
            or any(not isinstance(output, dict) for output in outputs)
            or len(output_bytes) > 32768
        ):
            raise LookupError("browser execution result invalid")
        public_steps = []
        for step in steps:
            if not isinstance(step, dict):
                raise LookupError("browser execution step invalid")
            title, status, duration = step.get("title"), step.get("status"), step.get("duration")
            if (
                not isinstance(title, str)
                or not isinstance(status, str)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise LookupError("browser execution step invalid")
            public_steps.append(
                {"title": title[:400], "status": status[:32], "durationMs": int(duration)}
            )
        public_evidence = []
        for item in evidence:
            if not isinstance(item, dict):
                raise LookupError("browser evidence invalid")
            evidence_id, kind, sha = item.get("object_ref"), item.get("kind"), item.get("sha256")
            if (
                not isinstance(evidence_id, str)
                or _ID.fullmatch(evidence_id) is None
                or kind not in _KINDS
                or not isinstance(sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", sha) is None
            ):
                raise LookupError("browser evidence invalid")
            evidence_url = (
                f"/api/v1/projects/{quote(scope.project_id, safe='')}"
                f"/workflows/{quote(task_id, safe='')}/browser-evidence/{evidence_id}"
            )
            public_evidence.append(
                {
                    "evidenceId": evidence_id,
                    "kind": kind,
                    "sha256": sha,
                    "url": evidence_url,
                }
            )
        return {
            "executionId": execution_id,
            "bundleId": bundle_id,
            "inputSha256": receipt["input_sha256"],
            "status": receipt["status"],
            "failureCode": receipt.get("failure_code"),
            "replayModelCalls": result.get("replay_model_calls", 0),
            "steps": public_steps,
            "outputs": outputs,
            "evidence": public_evidence,
        }

    async def read(
        self, scope: ProjectScopeContext, task_id: str, evidence_id: str
    ) -> tuple[bytes, str]:
        if _ID.fullmatch(evidence_id) is None:
            raise LookupError("browser evidence unavailable")
        snapshot = await self.store.snapshot(scope, task_id)
        for event in snapshot["events"]:
            if (
                event.kind == "browser.recording_saved"
                and event.payload.get("evidence_id") == evidence_id
            ):
                return await self.client.read_evidence(evidence_id)
        view = await self.project(scope, task_id, snapshot)
        if view is None or not any(item["evidenceId"] == evidence_id for item in view["evidence"]):
            raise LookupError("browser evidence unavailable")
        return await self.client.read_evidence(evidence_id)
