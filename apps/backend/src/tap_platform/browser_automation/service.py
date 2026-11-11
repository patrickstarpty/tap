"""Project-scoped browser automation authority and immutable input service."""

from __future__ import annotations

import hashlib
import uuid
import json
import re
from datetime import UTC, datetime, timedelta
from dataclasses import asdict
from typing import Any

from tap_platform.access import AccessPrincipal

from .contracts import BrowserBundle, BrowserLimits
from .control import ControlActor
from .live_frames import LiveFrameBroker
from .input_vault import InputVault
from .objects import BrowserObjectStore
from .policy import validate_origin, validate_url
from .store import BrowserStore

_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ACTIONS = {
    "navigate",
    "preflight",
    "snapshot",
    "click",
    "scroll",
    "fill",
    "select",
    "press",
    "wait",
    "screenshot",
    "read",
    "finish",
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
        self.frames = LiveFrameBroker(objects.root / ".live-frames")
        self.inputs = InputVault()

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

    @staticmethod
    def _network_mode(body: dict[str, Any]) -> str:
        mode = body.get("network_mode", "declared_origins")
        if mode not in ("declared_origins", "public_web"):
            raise Invalid("invalid browser network mode")
        return str(mode)

    def create_session(
        self,
        principal: AccessPrincipal,
        body: dict[str, Any],
        key: str,
        *,
        close_on_recording_stop: bool = False,
    ) -> dict[str, Any]:
        if "close_on_recording_stop" in body:
            raise Invalid("recording cleanup must come from trusted context")
        identity = (
            {**body, "close_on_recording_stop": True}
            if close_on_recording_stop
            else body
        )
        project = self._project(principal, "browser.sessions.create")
        prior = self._prior("session", project, key, identity)
        if prior is not None:
            return self._session(prior)
        task_id = self._id(body.get("task_id"), "task_id")
        origins = self._origins(body.get("allowed_origins"))
        network_mode = self._network_mode(body)
        deadline = body.get("deadline_seconds", 120)
        if type(deadline) is not int or not 1 <= deadline <= 600:
            raise Invalid("invalid deadline")
        version = body.get("control_version")
        if version not in (None, "browser-control-v1"):
            raise Invalid("invalid browser control version")
        row = self._insert(
            "session",
            project,
            key,
            identity,
            dict(
                task_id=task_id,
                status="pending",
                request={
                    "allowed_origins": origins,
                    "deadline_seconds": deadline,
                    "network_mode": network_mode,
                    **(
                        {"close_on_recording_stop": True}
                        if close_on_recording_stop
                        else {}
                    ),
                },
                observation=None,
                owner=None,
                lease_until=None,
                control_version=version,
                controller_kind="agent",
                controller_id=principal.principal_id,
                agent_controller_id=principal.principal_id,
                control_epoch=0,
                control_state="agent_active",
                deadline_at=(
                    datetime.now(UTC) + timedelta(seconds=deadline)
                ).isoformat(),
                next_action_sequence=1,
                cancel_requested=False,
                stop_receipt=None,
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

    def get_control(
        self, principal: AccessPrincipal, session_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.read")
        try:
            return self.store.get_control(project, self._id(session_id, "session_id"))
        except ValueError as exc:
            raise Forbidden(str(exc)) from exc

    def request_observer(
        self, principal: AccessPrincipal, session_id: str
    ) -> dict[str, Any]:
        from sqlalchemy import update
        from .store import sessions

        project = self._project(principal, "browser.sessions.read")
        with self.store.session_transaction() as connection:
            try:
                session = self.store.lock_session(
                    connection, project, self._id(session_id, "session_id")
                )
            except ValueError as exc:
                raise Forbidden(str(exc)) from exc
            control = self.store.control_receipt(session)
            if (
                session["control_version"] != "browser-control-v1"
                or session["status"] != "active"
                or control["control_state"] in {"blocked", "closed"}
            ):
                raise Conflict("live browser observation unavailable")
            lease = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
            connection.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(observer_until=lease)
            )
            return control

    def reserve_model(
        self,
        principal: AccessPrincipal,
        session_id: str,
        expected_epoch: int,
        request_id: str,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.create")
        try:
            return self.store.reserve_model(
                project,
                self._id(session_id, "session_id"),
                principal.principal_id,
                expected_epoch,
                self._key(request_id),
            )
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    def settle_model(
        self,
        principal: AccessPrincipal,
        session_id: str,
        request_id: str,
        *,
        outcome: str = "completed",
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.create")
        try:
            return self.store.settle_model(
                project,
                self._id(session_id, "session_id"),
                principal.principal_id,
                self._key(request_id),
                outcome=outcome,
            )
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    def request_control(
        self,
        principal: AccessPrincipal,
        session_id: str,
        operation: str,
        expected_epoch: int,
        key: str,
        *,
        actor: ControlActor,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.control")
        if not isinstance(actor, ControlActor) or actor.kind != "human":
            raise Forbidden("trusted human controller required")
        try:
            self.store.validate_actor_expiry(actor.expires_at)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc
        try:
            return self.store.request_control(
                project,
                self._id(session_id, "session_id"),
                operation,
                expected_epoch,
                self._key(key),
                actor.kind,
                actor.controller_id,
            )
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

    def cancel_session(
        self, principal: AccessPrincipal, session_id: str
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.cancel")
        session_id = self._id(session_id, "session_id")
        row = self.store.get("session", project, session_id)
        if row is None:
            raise Forbidden("browser session unavailable")
        self.store.request_session_cancel(project, session_id)
        self.inputs.discard_session(project, session_id)
        if getattr(self, "frames", None) is not None:
            self.frames.delete(session_id)
        return self._session(self.store.get("session", project, session_id) or row)

    @staticmethod
    def _session(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row["id"],
            "project_id": row["project_id"],
            "task_id": row["task_id"],
            "status": row["status"],
            "observation": row["observation"],
            "cancel_requested": row["cancel_requested"],
            "stop_receipt": row["stop_receipt"],
            "created_at": row["created_at"],
        }

    def create_action(
        self,
        principal: AccessPrincipal,
        session_id: str,
        body: dict[str, Any],
        key: str,
        *,
        actor: ControlActor | None = None,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.create")
        if any(
            name in body
            for name in (
                "actor",
                "actor_kind",
                "controller_id",
                "actor_expires_at",
                "expires_at",
                "input_ref",
            )
        ):
            raise Invalid("actor must come from trusted authorization context")
        actor = actor or ControlActor("agent", principal.principal_id)
        try:
            self.store.validate_actor_expiry(actor.expires_at)
        except ValueError as exc:
            raise Conflict(str(exc)) from exc
        identity = {
            "route_session_id": session_id,
            "body": body,
            "actor": asdict(actor),
        }
        prior = self._prior("action", project, key, identity)
        if prior is not None:
            return self._action(prior)
        session = self.store.get("session", project, self._id(session_id, "session_id"))
        if session is None:
            raise Forbidden("browser session unavailable")
        if body.get("type") not in _ACTIONS or len(canonical_bytes(body)) > 16_384:
            raise Invalid("invalid browser action")
        if session["status"] not in {"pending", "running", "active"}:
            raise Conflict("browser session unavailable")
        if body["type"] == "navigate":
            url = body.get("url")
            if not isinstance(url, str) or not 1 <= len(url) <= 4096:
                raise Invalid("invalid browser navigation URL")
            try:
                validate_url(
                    url,
                    tuple(session["request"].get("allowed_origins", ())),
                    network_mode=session["request"].get(
                        "network_mode", "declared_origins"
                    ),
                )
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
            if actor.kind == "human":
                viewport = body.get("viewport")
                if (
                    set(body)
                    - {"type", "url", "expected_epoch", "frame_sequence", "viewport"}
                    or type(body.get("frame_sequence")) is not int
                    or body["frame_sequence"] < 0
                    or not isinstance(viewport, dict)
                    or set(viewport) != {"width", "height"}
                    or any(
                        type(viewport[k]) is not int or not 1 <= viewport[k] <= 4096
                        for k in ("width", "height")
                    )
                ):
                    raise Invalid("human navigation requires current target-free frame")
        request = {
            name: value for name, value in body.items() if name != "expected_epoch"
        }
        input_ref = None
        if session.get("control_version") == "browser-control-v1" and body["type"] in {
            "fill",
            "select",
            "press",
        }:
            input_key = "key" if body["type"] == "press" else "value"
            allowed = {
                "type",
                "selector",
                "x",
                "y",
                "frame_sequence",
                "viewport",
                "preflight_action_id",
                input_key,
            }
            if (
                set(request) - allowed
                or not isinstance(request.get(input_key), str)
                or len(request[input_key]) > 4000
            ):
                raise Invalid("invalid controlled input")
            expiry = min(
                x
                for x in (session.get("deadline_at"), actor.expires_at)
                if x is not None
            )
            input_ref = self.inputs.put(
                project,
                session_id,
                {input_key: request.pop(input_key)},
                expires_at=expiry,
            )
            request["input_ref"] = input_ref
        try:
            row = self.store.insert_action_idempotent(
                project,
                session_id,
                self._key(key),
                digest(identity),
                request,
                actor_kind=actor.kind,
                controller_id=actor.controller_id,
                expected_epoch=body.get("expected_epoch"),
                actor_expires_at=actor.expires_at,
            )
        except BaseException as exc:
            if input_ref:
                self.inputs.discard(input_ref)
            if isinstance(exc, ValueError):
                raise Conflict(str(exc)) from None
            raise
        if input_ref and row["request"].get("input_ref") != input_ref:
            self.inputs.discard(input_ref)
        return self._action(row)

    def consume_input_for_dispatch(
        self, project_id: str, session_id: str, action_id: str
    ) -> dict[str, Any]:
        from .store import actions, now
        from sqlalchemy import select

        with self.store.session_transaction() as connection:
            session = self.store.lock_session(connection, project_id, session_id)
            row = (
                connection.execute(
                    select(actions).where(
                        actions.c.id == action_id,
                        actions.c.session_id == session_id,
                        actions.c.project_id == project_id,
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or row["status"] != "dispatching"
                or (row["lease_until"] or "") < now()
                or session["status"] != "active"
                or session["cancel_requested"]
                or session["control_state"] in {"blocked", "closed"}
                or session["control_epoch"] != row["control_epoch"]
                or (session.get("deadline_at") or "") <= now()
                or row["owner"] != session["owner"]
            ):
                raise ValueError("browser input dispatch unavailable")
            self.store.validate_actor_expiry(row["actor_expires_at"])
            reference = row["request"].get("input_ref")
            if not isinstance(reference, str):
                raise ValueError("browser input dispatch unavailable")
            return self.inputs.consume(project_id, session_id, reference)

    def list_actions(
        self,
        principal: AccessPrincipal,
        session_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.actions.read")
        try:
            rows = self.store.list_actions(
                project, self._id(session_id, "session_id"), after=after, limit=limit
            )
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        return {
            "actions": [self._action(row) for row in rows],
            "next_after": rows[-1]["sequence"] if rows else after,
        }

    def stop_recording(
        self,
        principal: AccessPrincipal,
        session_id: str,
        expected_epoch: int,
        key: str,
        *,
        actor: ControlActor,
    ) -> dict[str, Any]:
        project = self._project(principal, "browser.sessions.control")
        if not isinstance(actor, ControlActor) or actor.kind != "human":
            raise Forbidden("trusted human controller required")
        try:
            return self.store.stop_recording(
                project,
                self._id(session_id, "session_id"),
                expected_epoch,
                self._key(key),
                actor.controller_id,
                actor_expires_at=actor.expires_at,
            )
        except ValueError as exc:
            raise Conflict(str(exc)) from exc

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
        observation = row.get("observation") or {}
        bound = (observation.get("extracted") or {}).get("control_receipt") or {}
        return {
            "schema_version": "browser-control-v1",
            "command_id": row.get("command_id"),
            "actor_kind": row.get("actor_kind", "agent"),
            "control_epoch": row.get("control_epoch", 0),
            "operation": row["request"].get("type"),
            "pre_observation_sequence": bound.get("pre_observation_sequence"),
            "target": bound.get("target"),
            "args": bound.get("args", bound.get("arguments", {})),
            "post_observation_sequence": (row.get("observation") or {}).get("sequence"),
            "evidence_refs": (row.get("observation") or {}).get("evidence", []),
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
        network_mode = self._network_mode(body)
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
                network_mode=network_mode,
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
            "network_mode": row["network_mode"],
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
            network_mode=row["network_mode"],
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
            if old["status"] not in {"passed", "failed", "canceled", "unknown"}:
                raise Conflict("browser execution is not terminal")
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
        if "live_session_id" in body:
            return self._complete_live(project, task_id, bundle, body, key, limits)
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

    def _complete_live(self, project, task_id, bundle, body, key, limits):
        session_id = self._id(body.get("live_session_id"), "session_id")
        session = self.store.get("session", project, session_id)
        action = self.store.get(
            "action", project, self._id(body.get("finish_action_id"), "action_id")
        )
        if (
            session is None
            or session["task_id"] != task_id
            or bundle["task_id"] != task_id
            or action is None
            or action["session_id"] != session_id
        ):
            raise Forbidden("live browser provenance mismatch")
        observation = action.get("observation") or {}
        if (
            action["status"] != "completed"
            or action["request"].get("type") != "finish"
            or observation.get("error")
            or not isinstance(observation.get("result"), dict)
        ):
            raise Invalid("live browser completion not observed")
        steps = body.get("steps", [])
        if (
            not isinstance(steps, list)
            or not 1 <= len(steps) <= 100
            or any(
                not isinstance(x, dict)
                or not isinstance(x.get("title"), str)
                or len(x["title"]) > 400
                or x.get("status") != "passed"
                or not isinstance(x.get("duration"), (int, float))
                or not 0 <= x["duration"] <= 600000
                for x in steps
            )
        ):
            raise Invalid("invalid live business steps")
        refs = observation.get("evidence", [])
        if (
            not isinstance(refs, list)
            or not {"screenshot", "trace", "result"}.issubset(
                {x.get("kind") for x in refs}
            )
            or len(refs) not in {3, 4}
        ):
            raise Invalid("incomplete live evidence")
        execution_id = uuid.uuid4().hex
        cloned, public = [], []
        stamp = datetime.now(UTC).isoformat()
        for item in refs:
            self.verify_inspector_evidence(
                project, task_id, session_id, item["object_ref"]
            )
            source = self.store.get("evidence", project, item["object_ref"])
            assert source is not None
            if any(source[k] != item[k] for k in ("kind", "sha256", "size_bytes")):
                raise Invalid("live evidence metadata mismatch")
            if source["kind"] == "result":
                saved_result = json.loads(
                    self.objects.read(
                        source["object_ref"], source["sha256"], max_bytes=32768
                    )
                )
                if saved_result != observation["result"]:
                    raise Invalid("live business output differs from stored result")
            identity = uuid.uuid4().hex
            cloned.append(
                {
                    **source,
                    "id": identity,
                    "execution_id": execution_id,
                    "session_id": None,
                    "created_at": stamp,
                }
            )
            public.append(
                {
                    "kind": source["kind"],
                    "object_ref": identity,
                    "sha256": source["sha256"],
                    "size_bytes": source["size_bytes"],
                }
            )
        values = dict(
            id=execution_id,
            task_id=task_id,
            bundle_id=bundle["id"],
            input_sha256=bundle["input_sha256"],
            status="passed",
            request={
                "limits": asdict(limits),
                "mode": "live",
                "session_id": session_id,
            },
            owner=None,
            lease_until=None,
            cancel_requested=False,
            started_at=session["created_at"],
            finished_at=stamp,
            result=observation["result"],
            steps=steps,
            evidence=public,
            failure_code=None,
        )
        try:
            row = self.store.insert_idempotent(
                "execution",
                project,
                self._key(key),
                digest(body),
                values,
                evidence_rows=cloned,
            )
        except ValueError as error:
            raise Conflict(str(error)) from error
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
        return self._execution(row)

    def _execution(self, row: dict[str, Any]) -> dict[str, Any]:
        result = {
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
        if result["status"] == "passed":
            try:
                self.verify_evidence(
                    row["project_id"],
                    row["evidence"] or [],
                    task_id=row["task_id"],
                    execution_id=row["id"],
                )
            except (ValueError, FileNotFoundError, Forbidden):
                result.update(status="unknown", failure_code="evidence_unavailable")
        return result

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
                row["object_ref"], row["sha256"], max_bytes=80_000_000
            )
            if len(content) != row["size_bytes"]:
                raise ValueError("browser evidence size mismatch")
            kinds.add(row["kind"])
        if not {"screenshot", "trace", "result"}.issubset(kinds):
            raise ValueError("incomplete browser evidence")

    def verify_inspector_evidence(
        self, project: str, task_id: str, session_id: str, evidence_id: str
    ) -> None:
        session = self.store.get("session", project, session_id)
        row = self.store.get("evidence", project, evidence_id)
        if (
            session is None
            or session["task_id"] != task_id
            or row is None
            or row["task_id"] != task_id
            or row["session_id"] != session_id
            or row["execution_id"] is not None
            or row["kind"] not in {"screenshot", "trace", "result", "video"}
        ):
            raise Forbidden("browser evidence unavailable")
        content = self.objects.read(
            row["object_ref"],
            row["sha256"],
            max_bytes={
                "screenshot": 2_000_000,
                "trace": 5_000_000,
                "result": 32768,
                "video": 64_000_000,
            }[row["kind"]],
        )
        if len(content) != row["size_bytes"]:
            raise ValueError("browser evidence size mismatch")

    def read_evidence(
        self, principal: AccessPrincipal, evidence_id: str
    ) -> tuple[dict[str, Any], bytes]:
        project = self._project(principal, "browser.evidence.read")
        row = self.store.get("evidence", project, self._id(evidence_id, "evidence_id"))
        if row is None:
            raise Forbidden("browser evidence unavailable")
        data = self.objects.read(row["object_ref"], row["sha256"], max_bytes=80_000_000)
        return row, data
