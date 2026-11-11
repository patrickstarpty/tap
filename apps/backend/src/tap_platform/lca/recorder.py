"""Owner-bound recording reads over the shared browser ledger, never a controller."""

from dataclasses import asdict, replace
import hashlib
import json
from typing import Any

from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError
from datetime import UTC, datetime

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.store import idempotency as browser_idempotency

from .definition import validate_definition
from .recording import project_recording
from .service import LcaService
from .store import LcaConflict, LcaNotFound, recordings


class LcaRecorder:
    def __init__(self, service: LcaService) -> None:
        self.service = service
        self.browser = service.browser

    def principal(self, actor: AccessPrincipal) -> AccessPrincipal:
        return replace(
            self.service._browser_principal(actor),
            actions=frozenset(
                {
                    "browser.sessions.create",
                    "browser.sessions.read",
                    "browser.actions.read",
                    "browser.sessions.cancel",
                }
            ),
        )

    def start(
        self, actor: AccessPrincipal, flow_id: str, version: int, key: str
    ) -> dict[str, Any]:
        project, owner = self.service._authorize(actor, "lca.recordings.create")
        flow = self.service.store.get_flow(project, owner, flow_id)
        if flow["version"] != version:
            raise LcaConflict("draft changed before recording")
        definition = validate_definition(json.loads(flow["canonical_bytes"]))
        recording_key = (
            "lca-record-"
            + hashlib.sha256(
                json.dumps([project, owner, flow_id, key]).encode()
            ).hexdigest()
        )
        with self.browser.store.engine.connect() as connection:
            retry_id = connection.execute(
                select(browser_idempotency.c.resource_id).where(
                    browser_idempotency.c.project_id == project,
                    browser_idempotency.c.kind == "session",
                    browser_idempotency.c.key == recording_key,
                )
            ).scalar_one_or_none()
        for existing_recording in self.list(actor, flow_id):
            prior_row = self.browser.store.get(
                "session", project, existing_recording["session_id"]
            )
            if (
                prior_row
                and prior_row["id"] != retry_id
                and (
                    prior_row["status"] not in {"completed", "canceled"}
                    or not (prior_row.get("stop_receipt") or {}).get("stopped")
                )
            ):
                raise LcaConflict("previous recording teardown is not confirmed")
        session = self.browser.create_session(
            self.principal(actor),
            {
                "task_id": flow_id,
                "allowed_origins": definition.allowed_origins,
                "network_mode": "declared_origins",
                "deadline_seconds": 600,
                "control_version": "browser-control-v1",
            },
            recording_key,
            close_on_recording_stop=True,
        )
        context = dict(
            session_id=session["session_id"],
            project_id=project,
            actor_id=owner,
            flow_id=flow_id,
            draft_version=version,
            allowed_origins=definition.allowed_origins,
            created_at=datetime.now(UTC).isoformat(),
        )
        try:
            with self.service.store.engine.begin() as connection:
                connection.execute(insert(recordings).values(**context))
        except IntegrityError:
            with self.service.store.engine.connect() as connection:
                prior = (
                    connection.execute(
                        select(recordings).where(
                            recordings.c.session_id == session["session_id"]
                        )
                    )
                    .mappings()
                    .one()
                )
                if any(
                    prior[k] != context[k]
                    for k in (
                        "project_id",
                        "actor_id",
                        "flow_id",
                        "draft_version",
                        "allowed_origins",
                    )
                ):
                    raise LcaConflict("recording context conflict") from None
        return self.get(actor, flow_id, session["session_id"])

    def list(self, actor: AccessPrincipal, flow_id: str) -> list[dict[str, Any]]:
        project, owner = self.service._authorize(actor, "lca.recordings.read")
        self.service.store.get_flow(project, owner, flow_id)
        with self.service.store.engine.connect() as connection:
            ids = (
                connection.execute(
                    select(recordings.c.session_id)
                    .where(
                        recordings.c.project_id == project,
                        recordings.c.actor_id == owner,
                        recordings.c.flow_id == flow_id,
                    )
                    .order_by(recordings.c.created_at.desc())
                    .limit(100)
                )
                .scalars()
                .all()
            )
        return [self.get(actor, flow_id, sid) for sid in ids]

    def cancel(
        self, actor: AccessPrincipal, flow_id: str, session_id: str
    ) -> dict[str, Any]:
        self.service._authorize(actor, "lca.recordings.cancel")
        self.get(actor, flow_id, session_id)
        self.browser.cancel_session(self.principal(actor), session_id)
        return self.get(actor, flow_id, session_id)

    def get(
        self, actor: AccessPrincipal, flow_id: str, session_id: str
    ) -> dict[str, Any]:
        project, owner = self.service._authorize(actor, "lca.recordings.read")
        self.service.store.get_flow(project, owner, flow_id)
        with self.service.store.engine.connect() as connection:
            context = (
                connection.execute(
                    select(recordings).where(
                        recordings.c.session_id == session_id,
                        recordings.c.project_id == project,
                        recordings.c.actor_id == owner,
                        recordings.c.flow_id == flow_id,
                    )
                )
                .mappings()
                .first()
            )
        if context is None:
            raise LcaNotFound("recording unavailable")
        row = self.browser.store.get("session", project, session_id)
        if (
            row is None
            or row["task_id"] != flow_id
            or row.get("control_version") != "browser-control-v1"
        ):
            raise LcaNotFound("recording unavailable")
        principal = self.principal(actor)
        control = self.browser.get_control(principal, session_id)
        state = (
            "recording" if control["control_state"] == "human_active" else "starting"
        )
        if control["control_state"] in {"closed", "blocked"}:
            state = "canceled" if row["status"] == "canceled" else "unknown"
        result: dict[str, Any] = {
            "session_id": session_id,
            "draft_version": context["draft_version"],
            "cancel_requested": row["cancel_requested"],
            "flow_id": flow_id,
            "status": state,
            "control": control,
        }
        if not control.get("recording_stopped"):
            return result
        start, watermark = (
            control["recording_start_sequence"],
            control["recording_watermark"],
        )
        if (
            type(start) is not int
            or type(watermark) is not int
            or watermark - start >= 100
        ):
            raise LcaConflict("recording interval unavailable or exceeds 100 actions")
        # Always reread the bounded interval, not a cursor past pending rows.
        page = self.browser.list_actions(
            principal, session_id, after=start - 1, limit=200
        )
        rows = [r for r in page["actions"] if r["sequence"] <= watermark]
        terminal = {"completed", "failed", "unknown", "rejected", "canceled"}
        settled = [r for r in rows if r["status"] in terminal]
        missing = sorted(
            set(range(start, watermark + 1)) - {r["sequence"] for r in settled}
        )
        result.update(status="draining", watermark=watermark)
        if missing:
            result["projection"] = {
                "ready": False,
                "definition": None,
                "missing_sequences": missing,
                "excluded": [],
            }
            return result
        if any(
            r["actor_kind"] != "human"
            or r["control_epoch"] != control["recording_epoch"]
            for r in settled
        ):
            raise LcaConflict("recording ownership changed")
        projection = project_recording(
            settled, row["request"]["allowed_origins"], watermark, start_sequence=start
        )
        cleanup_required = row["request"].get("close_on_recording_stop")
        cleanup_complete = (
            row["status"] == "completed"
            and (row.get("stop_receipt") or {}).get("reason") == "recording_drained"
            and row["stop_receipt"].get("stopped") is True
        )
        result.update(
            status=(
                "draining"
                if cleanup_required
                and (
                    not row["cancel_requested"]
                    or (row.get("stop_receipt") or {}).get("reason")
                    == "recording_drained"
                )
                and not cleanup_complete
                and row["status"] in {"pending", "running", "active"}
                else "stopped"
                if cleanup_complete
                and not any(r["status"] == "unknown" for r in settled)
                else "canceled"
                if row["status"] == "canceled" and row["cancel_requested"]
                else "unknown"
                if row["cancel_requested"]
                or any(r["status"] == "unknown" for r in settled)
                else "stopped"
            ),
            outcome_unknown=any(r["status"] == "unknown" for r in settled),
            projection={
                "ready": projection.ready,
                "definition": projection.definition.model_dump(
                    exclude_none=True,
                    exclude={"bdd_steps"}
                    if "bdd_steps" not in projection.definition.model_fields_set
                    else set(),
                )
                if projection.definition
                else None,
                "missing_sequences": list(projection.missing_sequences),
                "excluded": [asdict(x) for x in projection.excluded],
            },
        )
        return result
