"""Serialized browser controller transitions and durable dispatch reservations.

Only trusted service/gateway code constructs ControlActor. Never deserialize it
from arbitrary request JSON. Runtime revocation is deliberately an explicit ack.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TYPE_CHECKING
from contextlib import AbstractContextManager
from sqlalchemy.engine import Connection, Engine

from sqlalchemy import insert, select, update


@dataclass(frozen=True)
class ControlActor:
    kind: Literal["agent", "human"]
    controller_id: str
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if (
            self.kind not in ("agent", "human")
            or not self.controller_id
            or len(self.controller_id) > 128
        ):
            raise ValueError("invalid control actor")
        if self.expires_at is not None:
            expiry = datetime.fromisoformat(self.expires_at)
            if expiry.tzinfo is None or expiry.utcoffset() != timedelta(0):
                raise ValueError("control actor expiry must be UTC")
            object.__setattr__(self, "expires_at", expiry.astimezone(UTC).isoformat())


class ControlStoreMixin:
    if TYPE_CHECKING:
        engine: Engine

        def session_transaction(self) -> AbstractContextManager[Connection]: ...
        def get(
            self, kind: str, project_id: str, resource_id: str
        ) -> dict[str, Any] | None: ...

    @staticmethod
    def validate_actor_expiry(expires_at: str | None) -> None:
        if expires_at is not None:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None or expiry <= datetime.now(UTC):
                raise ValueError("browser controller grant expired")

    @staticmethod
    def lock_session(
        connection: Connection, project_id: str, session_id: str
    ) -> dict[str, Any]:
        from .store import sessions

        row = (
            connection.execute(
                select(sessions)
                .where(
                    sessions.c.id == session_id,
                    sessions.c.project_id == project_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("browser session unavailable")
        return dict(row)

    @staticmethod
    def validate_controller(
        session: dict[str, Any],
        actor_kind: str,
        controller_id: str | None,
        expected_epoch: int | None,
    ) -> None:
        if session["control_version"] is None:
            if actor_kind != "agent":
                raise ValueError("legacy browser control is agent only")
            return
        if (
            type(expected_epoch) is not int
            or session["control_epoch"] != expected_epoch
            or session["controller_kind"] != actor_kind
            or session["controller_id"] != controller_id
            or session["control_state"] != f"{actor_kind}_active"
            or session["cancel_requested"]
            or (
                session["deadline_at"]
                and session["deadline_at"] <= datetime.now(UTC).isoformat()
            )
        ):
            raise ValueError("browser control conflict")

    @staticmethod
    def control_receipt(
        session: dict[str, Any], outstanding: str | None = None
    ) -> dict[str, Any]:
        closed = (
            session["cancel_requested"]
            or session["status"] not in ("pending", "running", "active")
            or (
                session["deadline_at"]
                and session["deadline_at"] <= datetime.now(UTC).isoformat()
            )
        )
        state = "closed" if closed else session["control_state"]
        return {
            "schema_version": "browser-control-v1",
            "session_id": session["id"],
            "task_id": session["task_id"],
            "control_state": state,
            "controller_kind": session["controller_kind"],
            "controller_id": session["controller_id"],
            "control_epoch": session["control_epoch"],
            "deadline_at": session["deadline_at"],
            "pending_action_id": outstanding,
            "recording_stopped": session.get("recording_stopped", False),
            "recording_watermark": session.get("recording_watermark"),
            "recording_start_sequence": session.get("recording_start_sequence"),
            "recording_epoch": session.get("recording_epoch"),
            "allowed_controls": (
                ["request_takeover"]
                if state == "agent_active"
                else ["hand_back"]
                if state == "human_active"
                else []
            )
            if session["control_version"]
            else [],
        }

    def get_control(self, project_id: str, session_id: str) -> dict[str, Any]:
        from .store import actions

        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            outstanding = connection.execute(
                select(actions.c.id)
                .where(
                    actions.c.session_id == session_id,
                    actions.c.status == "dispatching",
                )
                .order_by(actions.c.sequence)
            ).scalar()
            return self.control_receipt(session, outstanding)

    def request_control(
        self,
        project_id: str,
        session_id: str,
        operation: str,
        expected_epoch: int | None,
        key: str,
        actor_kind: str,
        controller_id: str | None,
    ) -> dict[str, Any]:
        from .store import actions, control_requests, sessions

        identity = hashlib.sha256(
            json.dumps([operation, expected_epoch, actor_kind, controller_id]).encode()
        ).hexdigest()
        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            prior = (
                connection.execute(
                    select(control_requests).where(
                        control_requests.c.project_id == project_id,
                        control_requests.c.session_id == session_id,
                        control_requests.c.key == key,
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["digest"] != identity:
                    raise ValueError("idempotency conflict")
                return dict(prior["receipt"])
            if (
                session["control_version"] != "browser-control-v1"
                or actor_kind != "human"
                or type(expected_epoch) is not int
                or expected_epoch != session["control_epoch"]
                or self.control_receipt(session)["control_state"]
                in ("closed", "blocked")
            ):
                raise ValueError("browser control conflict")
            if operation == "takeover" and session["control_state"] == "agent_active":
                changes = dict(
                    control_state="takeover_pending",
                    pending_controller_id=controller_id,
                    agent_controller_id=session["controller_id"],
                )
            elif (
                operation == "handback"
                and session["control_state"] == "human_active"
                and session["controller_id"] == controller_id
            ):
                changes = dict(
                    control_state="handback_pending",
                    pending_controller_id=session["agent_controller_id"],
                )
            else:
                raise ValueError("browser control conflict")
            # Claimed but unreserved actions have not reached runtime and are safe to reject.
            connection.execute(
                update(actions)
                .where(
                    actions.c.session_id == session_id,
                    actions.c.status.in_(("pending", "running")),
                )
                .values(status="rejected")
            )
            latest = self.latest_observation(connection, session)
            changes["handoff_observation_sequence"] = max(
                latest, (session["observation"] or {}).get("sequence", 0)
            )
            connection.execute(
                update(sessions).where(sessions.c.id == session_id).values(**changes)
            )
            outstanding = connection.execute(
                select(actions.c.id).where(
                    actions.c.session_id == session_id,
                    actions.c.status == "dispatching",
                )
            ).scalar()
            receipt = self.control_receipt({**session, **changes}, outstanding)
            connection.execute(
                insert(control_requests).values(
                    project_id=project_id,
                    session_id=session_id,
                    key=key,
                    digest=identity,
                    receipt=receipt,
                )
            )
            return receipt

    def complete_control(
        self,
        project_id: str,
        session_id: str,
        owner: str,
        *,
        write_channel_revoked: bool,
        expected_epoch: int,
        observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .store import actions, sessions, now, model_reservations

        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            states = (
                connection.execute(
                    select(actions.c.status).where(actions.c.session_id == session_id)
                )
                .scalars()
                .all()
            )
            model_states = (
                connection.execute(
                    select(model_reservations.c.status).where(
                        model_reservations.c.session_id == session_id
                    )
                )
                .scalars()
                .all()
            )
            state = self.control_receipt(session)["control_state"]
            changes: dict[str, Any] = {}
            if state == "closed":
                changes = {"control_state": "closed"}
            elif "unknown" in states or "unknown" in model_states:
                changes = {"control_state": "blocked"}
            elif (
                session["control_epoch"] == expected_epoch
                and state in ("takeover_pending", "handback_pending")
                and write_channel_revoked
                and "dispatching" not in states
                and "reserved" not in model_states
                and session["owner"] == owner
                and (session["lease_until"] or "") >= now()
            ):
                latest = self.latest_observation(connection, session)
                fresh = (
                    observation
                    and not observation.get("error")
                    and observation.get("session_id", session_id) == session_id
                    and type(observation.get("sequence")) is int
                    and observation["sequence"]
                    > max(latest, session["handoff_observation_sequence"])
                )
                if state == "takeover_pending" or fresh:
                    kind = "human" if state == "takeover_pending" else "agent"
                    changes = dict(
                        control_state=f"{kind}_active",
                        controller_kind=kind,
                        controller_id=session["pending_controller_id"],
                        pending_controller_id=None,
                        control_epoch=session["control_epoch"] + 1,
                    )
                    if fresh and observation is not None:
                        changes["observation"] = observation
                        changes["next_action_sequence"] = max(
                            session["next_action_sequence"], observation["sequence"] + 1
                        )
            if (
                changes.get("control_state") == "human_active"
                and session["recording_start_sequence"] is None
                and not session["recording_stopped"]
            ):
                changes["recording_start_sequence"] = changes.get(
                    "next_action_sequence", session["next_action_sequence"]
                )
                changes["recording_epoch"] = changes["control_epoch"]
            if changes:
                connection.execute(
                    update(sessions)
                    .where(sessions.c.id == session_id)
                    .values(**changes)
                )
            return self.control_receipt({**session, **changes})

    def claim_control_action(
        self, project_id: str, owner: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        from .store import actions, now

        with self.engine.connect() as connection:
            candidates = (
                connection.execute(
                    select(actions)
                    .where(
                        actions.c.project_id == project_id,
                        actions.c.status == "pending",
                    )
                    .order_by(actions.c.created_at)
                )
                .mappings()
                .all()
            )
        for candidate in candidates:
            with self.session_transaction() as connection:
                session = self.lock_session(
                    connection, project_id, candidate["session_id"]
                )
                if (
                    session["owner"] != owner
                    or session["status"] != "active"
                    or session["cancel_requested"]
                    or (session["lease_until"] or "") < now()
                ):
                    continue
                older = connection.execute(
                    select(actions.c.id).where(
                        actions.c.session_id == session["id"],
                        actions.c.sequence < candidate["sequence"],
                        actions.c.status.not_in(("completed", "rejected", "failed")),
                    )
                ).first()
                if older:
                    continue
                try:
                    self.validate_actor_expiry(candidate.get("actor_expires_at"))
                    self.validate_controller(
                        session,
                        candidate["actor_kind"],
                        candidate["controller_id"],
                        candidate["control_epoch"],
                    )
                except ValueError:
                    connection.execute(
                        update(actions)
                        .where(
                            actions.c.id == candidate["id"],
                            actions.c.status == "pending",
                        )
                        .values(status="rejected")
                    )
                    continue
                lease = (
                    datetime.now(UTC) + timedelta(seconds=lease_seconds)
                ).isoformat()
                result = connection.execute(
                    update(actions)
                    .where(
                        actions.c.id == candidate["id"], actions.c.status == "pending"
                    )
                    .values(status="running", owner=owner, lease_until=lease)
                )
                if result.rowcount:
                    return {
                        **candidate,
                        "status": "running",
                        "owner": owner,
                        "lease_until": lease,
                    }
        return None

    def reserve_dispatch(
        self, project_id: str, action_id: str, owner: str
    ) -> dict[str, Any] | None:
        from .store import actions, now

        candidate = self.get("action", project_id, action_id)
        if candidate is None:
            return None
        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, candidate["session_id"])
            row = (
                connection.execute(select(actions).where(actions.c.id == action_id))
                .mappings()
                .one()
            )
            if row["status"] not in ("pending", "running"):
                return None
            try:
                self.validate_actor_expiry(row.get("actor_expires_at"))
                self.validate_controller(
                    session,
                    row["actor_kind"],
                    row["controller_id"],
                    row["control_epoch"],
                )
            except ValueError:
                connection.execute(
                    update(actions)
                    .where(actions.c.id == action_id)
                    .values(status="rejected")
                )
                return None
            if (
                row["status"] != "running"
                or row["owner"] != owner
                or (row["lease_until"] or "") < now()
                or session["owner"] != owner
                or session["status"] != "active"
                or session["cancel_requested"]
                or (session["lease_until"] or "") < now()
            ):
                return None
            connection.execute(
                update(actions)
                .where(actions.c.id == action_id)
                .values(status="dispatching")
            )
            return {**row, "status": "dispatching"}

    def reserve_model(
        self,
        project_id: str,
        session_id: str,
        controller_id: str | None,
        expected_epoch: int | None,
        request_id: str,
    ) -> dict[str, Any]:
        from .store import model_reservations

        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            prior = (
                connection.execute(
                    select(model_reservations).where(
                        model_reservations.c.project_id == project_id,
                        model_reservations.c.session_id == session_id,
                        model_reservations.c.request_id == request_id,
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if (
                    prior["controller_id"] != controller_id
                    or prior["control_epoch"] != expected_epoch
                ):
                    raise ValueError("model reservation conflict")
                return {"request_id": request_id, "status": "duplicate"}
            try:
                if session["control_version"] != "browser-control-v1" or session[
                    "status"
                ] not in ("pending", "running", "active"):
                    raise ValueError("browser control unavailable")
                self.validate_controller(
                    session, "agent", controller_id, expected_epoch
                )
            except ValueError:
                return {"request_id": request_id, "status": "rejected"}
            connection.execute(
                insert(model_reservations).values(
                    project_id=project_id,
                    session_id=session_id,
                    request_id=request_id,
                    controller_id=controller_id,
                    control_epoch=expected_epoch,
                    status="reserved",
                )
            )
            return {"request_id": request_id, "status": "reserved"}

    def settle_model(
        self,
        project_id: str,
        session_id: str,
        controller_id: str | None,
        request_id: str,
        *,
        outcome: str = "completed",
    ) -> dict[str, Any]:
        from .store import model_reservations, sessions

        if outcome not in ("completed", "unknown"):
            raise ValueError("invalid model reservation outcome")
        with self.session_transaction() as connection:
            self.lock_session(connection, project_id, session_id)
            criteria = (
                model_reservations.c.project_id == project_id,
                model_reservations.c.session_id == session_id,
                model_reservations.c.request_id == request_id,
                model_reservations.c.controller_id == controller_id,
            )
            prior = (
                connection.execute(select(model_reservations).where(*criteria))
                .mappings()
                .first()
            )
            if prior is None:
                raise ValueError("model reservation unavailable")
            if prior["status"] != "reserved":
                return {"request_id": request_id, "status": prior["status"]}
            connection.execute(
                update(model_reservations).where(*criteria).values(status=outcome)
            )
            if outcome == "unknown":
                connection.execute(
                    update(sessions)
                    .where(sessions.c.id == session_id)
                    .values(control_state="blocked")
                )
            return {"request_id": request_id, "status": outcome}

    def pending_control(
        self, project_id: str, session_id: str, owner: str
    ) -> dict[str, Any] | None:
        from .store import actions, model_reservations, now

        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            if (
                session["owner"] != owner
                or (session["lease_until"] or "") < now()
                or self.control_receipt(session)["control_state"]
                not in ("takeover_pending", "handback_pending")
            ):
                return None
            action = connection.execute(
                select(actions.c.id).where(
                    actions.c.session_id == session_id,
                    actions.c.status.in_(("dispatching", "unknown")),
                )
            ).first()
            model = connection.execute(
                select(model_reservations.c.request_id).where(
                    model_reservations.c.session_id == session_id,
                    model_reservations.c.status.in_(("reserved", "unknown")),
                )
            ).first()
            return None if action or model else session

    @staticmethod
    def validate_preflight(
        connection: Connection,
        session: dict[str, Any],
        request: dict[str, Any],
        actor_kind: str,
        controller_id: str | None,
    ) -> dict[str, Any]:
        from .store import actions

        if request.get("type") == "preflight":
            if set(request) - {
                "type",
                "operation",
                "selector",
                "x",
                "y",
                "frame_sequence",
                "viewport",
            } or request.get("operation") not in {"fill", "select", "press"}:
                raise ValueError("invalid value-free preflight")
            ControlStoreMixin.preflight_target_identity(request)
        if session["control_version"] != "browser-control-v1" or request.get(
            "type"
        ) not in {"fill", "select", "press"}:
            return request
        proof = (
            connection.execute(
                select(actions).where(
                    actions.c.id == request.get("preflight_action_id"),
                    actions.c.session_id == session["id"],
                    actions.c.project_id == session["project_id"],
                )
            )
            .mappings()
            .first()
        )
        if (
            proof is None
            or proof["status"] != "completed"
            or proof["request"].get("type") != "preflight"
            or proof["control_epoch"] != session["control_epoch"]
            or proof["controller_id"] != controller_id
            or proof["actor_kind"] != actor_kind
            or proof["sequence"] != session["next_action_sequence"] - 1
        ):
            raise ValueError("trusted current preflight required")
        observation = proof["observation"] or {}
        bound = (observation.get("extracted") or {}).get("preflight") or {}
        if (
            observation.get("error")
            or bound.get("safe") is not True
            or bound.get("operation") != request["type"]
            or ControlStoreMixin.preflight_target_identity(proof["request"])
            != ControlStoreMixin.preflight_target_identity(request)
            or proof["request"].get("operation") != request["type"]
            or (
                request.get("selector") is not None
                and bound.get("selector") != request.get("selector")
            )
            or not isinstance(bound.get("target"), dict)
        ):
            raise ValueError("preflight target mismatch")
        return {
            **request,
            "target": bound["target"],
            "pre_observation_sequence": bound.get(
                "pre_observation_sequence", observation["sequence"]
            ),
        }

    @staticmethod
    def preflight_target_identity(request: dict[str, Any]) -> dict[str, Any]:
        selector = request.get("selector")
        coordinates = "x" in request or "y" in request
        if selector is not None:
            if (
                not isinstance(selector, str)
                or not selector
                or len(selector) > 2000
                or coordinates
            ):
                raise ValueError("invalid preflight target")
            identity: dict[str, Any] = {"selector": selector}
        else:
            if not all(
                type(request.get(axis)) in (int, float) and math.isfinite(request[axis])
                for axis in ("x", "y")
            ):
                raise ValueError("invalid preflight coordinates")
            identity = {"x": request["x"], "y": request["y"]}
        viewport = request.get("viewport")
        if viewport is not None or coordinates:
            if (
                not isinstance(viewport, dict)
                or set(viewport) != {"width", "height"}
                or any(
                    type(viewport[k]) is not int or not 1 <= viewport[k] <= 4096
                    for k in ("width", "height")
                )
            ):
                raise ValueError("invalid preflight viewport")
            identity["viewport"] = viewport
        if "frame_sequence" in request or coordinates:
            if (
                type(request.get("frame_sequence")) is not int
                or request["frame_sequence"] < 0
            ):
                raise ValueError("invalid preflight frame")
            identity["frame_sequence"] = request["frame_sequence"]
        if coordinates:
            assert isinstance(viewport, dict)
            if not (
                0 <= request["x"] < viewport["width"]
                and 0 <= request["y"] < viewport["height"]
            ):
                raise ValueError("preflight coordinates outside viewport")
        return identity

    def list_actions(
        self, project_id: str, session_id: str, *, after: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        from .store import actions

        if (
            type(after) is not int
            or after < 0
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise ValueError("invalid action page")
        with self.session_transaction() as connection:
            self.lock_session(connection, project_id, session_id)
            rows = (
                connection.execute(
                    select(actions)
                    .where(
                        actions.c.project_id == project_id,
                        actions.c.session_id == session_id,
                        actions.c.sequence > after,
                    )
                    .order_by(actions.c.sequence)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    def stop_recording(
        self,
        project_id: str,
        session_id: str,
        expected_epoch: int,
        key: str,
        controller_id: str,
        *,
        actor_expires_at: str | None = None,
    ) -> dict[str, Any]:
        from .store import actions, control_requests, sessions

        identity = hashlib.sha256(
            json.dumps(
                ["stop_recording", expected_epoch, "human", controller_id]
            ).encode()
        ).hexdigest()
        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
            self.validate_actor_expiry(actor_expires_at)
            prior = (
                connection.execute(
                    select(control_requests).where(
                        control_requests.c.project_id == project_id,
                        control_requests.c.session_id == session_id,
                        control_requests.c.key == key,
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["digest"] != identity:
                    raise ValueError("idempotency conflict")
                receipt = dict(prior["receipt"])
            else:
                if self.control_receipt(session)["control_state"] != "human_active":
                    raise ValueError("browser control conflict")
                self.validate_controller(
                    session, "human", controller_id, expected_epoch
                )
                watermark = (
                    session["recording_watermark"]
                    if session["recording_stopped"]
                    else session["next_action_sequence"] - 1
                )
                connection.execute(
                    update(sessions)
                    .where(sessions.c.id == session_id)
                    .values(recording_stopped=True, recording_watermark=watermark)
                )
                receipt = {
                    "schema_version": "browser-control-v1",
                    "session_id": session_id,
                    "control_epoch": expected_epoch,
                    "watermark": watermark,
                    "recording_start_sequence": session["recording_start_sequence"],
                    "recording_epoch": session["recording_epoch"],
                }
                connection.execute(
                    insert(control_requests).values(
                        project_id=project_id,
                        session_id=session_id,
                        key=key,
                        digest=identity,
                        receipt=receipt,
                    )
                )
            states = (
                connection.execute(
                    select(actions.c.status).where(
                        actions.c.project_id == project_id,
                        actions.c.session_id == session_id,
                        actions.c.sequence <= receipt["watermark"],
                    )
                )
                .scalars()
                .all()
            )
            status = (
                "unknown"
                if "unknown" in states
                else "pending"
                if any(s in {"pending", "running", "dispatching"} for s in states)
                else "completed"
            )
            return {**receipt, "status": status}

    def confirm_dispatch(
        self, project_id: str, session_id: str, action_id: str, owner: str
    ) -> bool:
        """Revalidate after awaited preparation, before the runtime write call."""
        from .store import actions, now

        with self.session_transaction() as connection:
            session = self.lock_session(connection, project_id, session_id)
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
            if row is None or row["status"] != "dispatching" or row["owner"] != owner:
                return False
            try:
                self.validate_actor_expiry(row["actor_expires_at"])
                if (
                    session["status"] != "active"
                    or session["cancel_requested"]
                    or session["control_state"] in {"blocked", "closed"}
                    or session["control_epoch"] != row["control_epoch"]
                    or session["owner"] != owner
                    or (session["lease_until"] or "") < now()
                    or (row["lease_until"] or "") < now()
                    or (session.get("deadline_at") and session["deadline_at"] <= now())
                ):
                    raise ValueError("browser dispatch authority revoked")
            except ValueError:
                connection.execute(
                    update(actions)
                    .where(actions.c.id == action_id)
                    .values(status="rejected")
                )
                return False
            return True

    @staticmethod
    def latest_observation(connection: Connection, session: dict[str, Any]) -> int:
        from .store import actions

        observations = (
            connection.execute(
                select(actions.c.observation).where(
                    actions.c.session_id == session["id"],
                    actions.c.project_id == session["project_id"],
                )
            )
            .scalars()
            .all()
        )
        sequences = [
            value.get("sequence")
            for value in [session.get("observation"), *observations]
            if isinstance(value, dict)
        ]
        return max((value for value in sequences if type(value) is int), default=0)
