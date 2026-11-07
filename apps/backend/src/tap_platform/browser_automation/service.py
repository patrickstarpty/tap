"""Project-scoped browser automation authority and immutable input service."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from dataclasses import asdict
from typing import Any

from tap_platform.access import AccessPrincipal

from .contracts import BrowserBundle, BrowserLimits
from .objects import BrowserObjectStore
from .policy import validate_origin
from .store import BrowserStore

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = {
    "navigate",
    "snapshot",
    "click",
    "fill",
    "select",
    "press",
    "wait",
    "screenshot",
}


class Invalid(ValueError):
    pass


class Conflict(ValueError):
    pass


class Forbidden(PermissionError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class BrowserService:
    def __init__(self, store: BrowserStore, objects: BrowserObjectStore) -> None:
        self.store = store
        self.objects = objects

    def _project(self, principal: AccessPrincipal, action: str) -> str:
        if (
            not isinstance(principal, AccessPrincipal)
            or not principal.enabled
            or principal.expires_at <= datetime.now(UTC)
            or principal.audience != "tap-browser"
            or action not in principal.actions
        ):
            raise Forbidden("browser action forbidden")
        return principal.project_id

    @staticmethod
    def _id(value: Any, name: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise Invalid(f"invalid {name}")
        return value

    @staticmethod
    def _key(value: str) -> str:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 128
            or not value.isascii()
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
        ):
            raise Invalid("invalid idempotency key")
        return value

    @staticmethod
    def _origins(value: Any) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not 1 <= len(value) <= 8
            or not all(isinstance(x, str) and validate_origin(x) == x for x in value)
        ):
            raise Invalid("invalid allowed origins")
        return tuple(value)

    def _prior(
        self, kind: str, project: str, key: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            return self.store.existing_idempotent(
                kind, project, self._key(key), digest(body)
            )
        except ValueError as exc:
            if str(exc) == "idempotency conflict":
                raise Conflict(str(exc)) from None
            raise

    def _insert(
        self,
        kind: str,
        project: str,
        key: str,
        body: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.store.insert_idempotent(
                kind, project, self._key(key), digest(body), values
            )
        except ValueError as exc:
            if str(exc) == "idempotency conflict":
                raise Conflict(str(exc)) from None
            raise

    def create_session(
        self, principal: AccessPrincipal, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.create")
        prior = self._prior("session", project, key, body)
        if prior is not None:
            return self._session(prior)
        task_id = self._id(body.get("task_id"), "task_id")
        origins = self._origins(body.get("allowed_origins"))
        deadline = body.get("deadline_seconds", 120)
        if type(deadline) is not int or not 1 <= deadline <= 600:
            raise Invalid("invalid deadline")
        row = self._insert(
            "session",
            project,
            key,
            body,
            dict(
                task_id=task_id,
                status="pending",
                request={"allowed_origins": origins, "deadline_seconds": deadline},
                observation=None,
                owner=None,
                lease_until=None,
            ),
        )
        return self._session(row)

    def get_session(
        self, principal: AccessPrincipal, session_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.read")
        row = self.store.get("session", project, self._id(session_id, "session_id"))
        if row is None:
            raise Forbidden("browser session unavailable")
        return self._session(row)

    @staticmethod
    def _session(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row["id"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "observation": row["observation"],
            "created_at": row["created_at"],
        }

    def create_action(
        self,
        principal: AccessPrincipal,
        session_id: str,
        body: dict[str, Any],
        key: str,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.create")
        prior = self._prior("action", project, key, {"session_id": session_id, **body})
        if prior is not None:
            return self._action(prior)
        session = self.store.get("session", project, self._id(session_id, "session_id"))
        if session is None:
            raise Forbidden("browser session unavailable")
        if body.get("type") not in _ACTIONS or len(canonical_bytes(body)) > 16_384:
            raise Invalid("invalid browser action")
        if session["status"] not in {"pending", "running", "active"}:
            raise Conflict("browser session unavailable")
        row = self._insert(
            "action",
            project,
            key,
            {"session_id": session_id, **body},
            dict(
                session_id=session_id,
                sequence=self.store.next_sequence(project, session_id),
                status="pending",
                request=body,
                observation=None,
                owner=None,
            ),
        )
        return self._action(row)

    def get_action(
        self, principal: AccessPrincipal, session_id: str, action_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.read")
        row = self.store.get("action", project, self._id(action_id, "action_id"))
        if row is None or row["session_id"] != session_id:
            raise Forbidden("browser action unavailable")
        return self._action(row)

    @staticmethod
    def _action(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "action_id": row["id"],
            "session_id": row["session_id"],
            "sequence": row["sequence"],
            "status": row["status"],
            "observation": row["observation"],
        }

    def create_bundle(
        self, principal: AccessPrincipal, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.bundles.create")
        prior = self._prior("bundle", project, key, body)
        if prior is not None:
            return self._bundle(prior)
        task_id = self._id(body.get("task_id"), "task_id")
        if body.get("entry_name", "flow.spec.ts") != "flow.spec.ts" or not isinstance(
            body.get("script"), str
        ):
            raise Invalid("invalid browser entry")
        script = body["script"].encode()
        if (
            not script
            or len(script) > 256_000
            or hashlib.sha256(script).hexdigest() != body.get("sha256")
        ):
            raise Invalid("browser bundle hash mismatch")
        inputs = body.get("input_data")
        if not isinstance(inputs, dict):
            raise Invalid("input_data must be an object")
        frozen = canonical_bytes(inputs)
        if len(frozen) > 1_000_000 or hashlib.sha256(frozen).hexdigest() != body.get(
            "input_sha256"
        ):
            raise Invalid("frozen input hash mismatch")
        origins = self._origins(body.get("allowed_origins"))
        self._key(key)
        script_ref, _, _ = self.objects.persist(
            script, kind="script", max_bytes=256_000
        )
        input_ref, _, _ = self.objects.persist(
            frozen, kind="input", max_bytes=1_000_000
        )
        row = self._insert(
            "bundle",
            project,
            key,
            body,
            dict(
                task_id=task_id,
                sha256=body["sha256"],
                script_ref=script_ref,
                input_sha256=body["input_sha256"],
                input_ref=input_ref,
                allowed_origins=origins,
            ),
        )
        return self._bundle(row)

    @staticmethod
    def _bundle(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "bundle_id": row["id"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "sha256": row["sha256"],
            "input_sha256": row["input_sha256"],
            "allowed_origins": row["allowed_origins"],
            "created_at": row["created_at"],
            "entry_name": "flow.spec.ts",
        }

    def frozen_bundle(
        self, project: str, bundle_id: str
    ) -> tuple[BrowserBundle, dict[str, Any]]:
        row = self.store.get("bundle", project, bundle_id)
        if row is None:
            raise Forbidden("browser bundle unavailable")
        script = self.objects.read(row["script_ref"], row["sha256"], max_bytes=256_000)
        data = self.objects.read(
            row["input_ref"], row["input_sha256"], max_bytes=1_000_000
        )
        return BrowserBundle(
            row["id"],
            project,
            row["sha256"],
            script,
            row["input_sha256"],
            tuple(row["allowed_origins"]),
            row["created_at"],
        ), json.loads(data)

    def get_bundle(self, principal: AccessPrincipal, bundle_id: str) -> dict[str, Any]:
        project = self._project(principal, "browser.bundles.read")
        row = self.store.get("bundle", project, self._id(bundle_id, "bundle_id"))
        if row is None:
            raise Forbidden("browser bundle unavailable")
        return self._bundle(row)

    def create_execution(
        self, principal: AccessPrincipal, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.executions.create")
        prior = self._prior("execution", project, key, body)
        if prior is not None:
            return self._execution(prior)
        task_id = self._id(body.get("task_id"), "task_id")
        if "rerun_of" in body:
            if "limits" in body:
                raise Invalid("rerun limits are frozen")
            old = self.store.get(
                "execution", project, self._id(body["rerun_of"], "rerun_of")
            )
            if old is None:
                raise Forbidden("browser execution unavailable")
            bundle_id, input_sha = old["bundle_id"], old["input_sha256"]
            limits_value = old["request"]["limits"]
        else:
            limits_value = body.get("limits", {})
            bundle_id = self._id(body.get("bundle_id"), "bundle_id")
            input_sha = body.get("input_sha256")
        if not isinstance(limits_value, dict):
            raise Invalid("invalid browser limits")
        try:
            limits = BrowserLimits(**limits_value)
        except (TypeError, ValueError) as exc:
            raise Invalid("invalid browser limits") from exc
        bundle = self.store.get("bundle", project, bundle_id)
        if bundle is None:
            raise Forbidden("browser bundle unavailable")
        if input_sha != bundle["input_sha256"]:
            raise Invalid("frozen input hash mismatch")
        self.frozen_bundle(project, bundle_id)
        row = self._insert(
            "execution",
            project,
            key,
            body,
            dict(
                task_id=task_id,
                bundle_id=bundle_id,
                input_sha256=input_sha,
                status="pending",
                request={"limits": asdict(limits)},
                owner=None,
                lease_until=None,
                cancel_requested=False,
                started_at=None,
                finished_at=None,
                result=None,
                steps=None,
                evidence=None,
                failure_code=None,
            ),
        )
        return self._execution(row)

    def get_execution(
        self, principal: AccessPrincipal, execution_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.executions.read")
        row = self.store.get(
            "execution", project, self._id(execution_id, "execution_id")
        )
        if row is None:
            raise Forbidden("browser execution unavailable")
        result = self._execution(row)
        if result["status"] == "passed":
            try:
                self.verify_evidence(
                    project,
                    row["evidence"] or [],
                    task_id=row["task_id"],
                    execution_id=row["id"],
                )
            except (ValueError, FileNotFoundError, Forbidden):
                result.update(status="unknown", failure_code="evidence_unavailable")
        return result

    @staticmethod
    def _execution(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "execution_id": row["id"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "bundle_id": row["bundle_id"],
            "input_sha256": row["input_sha256"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": row["result"],
            "steps": row["steps"] or [],
            "evidence": row["evidence"] or [],
            "failure_code": row["failure_code"],
        }

    def cancel_execution(
        self, principal: AccessPrincipal, execution_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.executions.cancel")
        row = self.store.get(
            "execution", project, self._id(execution_id, "execution_id")
        )
        if row is None:
            raise Forbidden("browser execution unavailable")
        if row["status"] in {"pending", "running"}:
            self.store.update("execution", project, execution_id, cancel_requested=True)
            row = self.store.get("execution", project, execution_id)
            assert row is not None
        return self._execution(row)

    def verify_evidence(
        self,
        project: str,
        refs: list[dict[str, Any]],
        *,
        task_id: str,
        execution_id: str,
    ) -> None:
        kinds = set()
        for item in refs:
            row = self.store.get("evidence", project, item["object_ref"])
            if (
                row is None
                or row["task_id"] != task_id
                or row["execution_id"] != execution_id
                or row["sha256"] != item["sha256"]
                or row["size_bytes"] != item["size_bytes"]
                or row["kind"] != item["kind"]
            ):
                raise Forbidden("browser evidence unavailable")
            content = self.objects.read(
                row["object_ref"], row["sha256"], max_bytes=10_000_000
            )
            if len(content) != row["size_bytes"]:
                raise ValueError("browser evidence size mismatch")
            kinds.add(row["kind"])
        if not {"screenshot", "trace", "result"}.issubset(kinds):
            raise ValueError("incomplete browser evidence")

    def read_evidence(
        self, principal: AccessPrincipal, evidence_id: str
    ) -> tuple[dict[str, Any], bytes]:
        project = self._project(principal, "browser.evidence.read")
        row = self.store.get("evidence", project, self._id(evidence_id, "evidence_id"))
        if row is None:
            raise Forbidden("browser evidence unavailable")
        data = self.objects.read(row["object_ref"], row["sha256"], max_bytes=10_000_000)
        return row, data
