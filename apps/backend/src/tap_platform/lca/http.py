"""LCA HTTP adapter; authentication must be injected by the trusted gateway."""

import json
import secrets
from datetime import UTC, datetime, timedelta
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.service import Conflict, Forbidden

from .assets import safe_filename
from .service import LcaService
from .recorder import LcaRecorder
from .store import LcaConflict, LcaNotFound


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str = Field(min_length=1, max_length=1024)
    content_base64: str = Field(min_length=1, max_length=341336)


class FlowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=256)
    definition: dict[str, Any]


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    definition: dict[str, Any]


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    verified_run_id: str = Field(min_length=1, max_length=80)


class ReleaseRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


def _public(record: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: value
        for key, value in record.items()
        if key
        not in {
            "canonical_bytes",
            "execution_key",
            "execution_request",
            "actor_id",
            "identity_digest",
        }
    }
    if "canonical_bytes" in record:
        result["definition"] = json.loads(record["canonical_bytes"])
    return result


def create_lca_router(
    service: LcaService | None = None,
    authorize: Callable[[Request, str], AccessPrincipal] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/lca", tags=["lca"])

    def invoke(
        request: Request,
        project_id: str,
        operation: Callable[[LcaService, AccessPrincipal], Any],
    ) -> Any:
        if service is None or authorize is None:
            raise HTTPException(503, "LCA capability unavailable")
        try:
            actor = authorize(request, project_id)
            if actor.project_id != project_id:
                raise PermissionError("LCA project forbidden")
            return operation(service, actor)
        except (PermissionError, Forbidden) as exc:
            raise HTTPException(403, str(exc)) from None
        except LcaNotFound:
            raise HTTPException(404, "LCA resource unavailable") from None
        except (LcaConflict, Conflict) as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError:
            raise HTTPException(422, "Invalid LCA request") from None

    def key(request: Request) -> str:
        value = request.headers.get("idempotency-key")
        if (
            value is None
            or not 1 <= len(value) <= 128
            or not value.isascii()
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
        ):
            raise HTTPException(400, "Valid Idempotency-Key required")
        return value

    @router.get("/assets")
    def list_assets(project_id: str, request: Request) -> Any:
        return invoke(request, project_id, lambda svc, actor: svc.list_assets(actor))

    @router.post("/assets", status_code=201)
    def create_asset(project_id: str, body: AssetInput, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: svc.create_asset(
                actor, key(request), body.filename, body.content_base64
            ),
        )

    @router.get("/flows")
    def list_flows(project_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: [_public(row) for row in svc.list_flows(actor)],
        )

    @router.post("/flows", status_code=201)
    def create_flow(project_id: str, body: FlowInput, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(
                svc.create_flow(actor, key(request), body.title, body.definition)
            ),
        )

    @router.get("/flows/{flow_id}")
    def get_flow(project_id: str, flow_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(svc.get_flow(actor, flow_id)),
        )

    @router.put("/flows/{flow_id}/draft")
    def save_draft(
        project_id: str, flow_id: str, body: DraftInput, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(
                svc.save_draft(actor, flow_id, body.expected_version, body.definition)
            ),
        )

    @router.post("/flows/{flow_id}/recordings", status_code=202)
    def start_recording(
        project_id: str, flow_id: str, body: RunInput, request: Request
    ) -> Any:
        if body.parameters:
            raise HTTPException(422, "Recording does not accept replay parameters")
        return invoke(
            request,
            project_id,
            lambda svc, actor: LcaRecorder(svc).start(
                actor, flow_id, body.expected_version, key(request)
            ),
        )

    @router.get("/flows/{flow_id}/recordings")
    def list_recordings(project_id: str, flow_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: LcaRecorder(svc).list(actor, flow_id),
        )

    @router.get("/flows/{flow_id}/recordings/{session_id}")
    def get_recording(
        project_id: str, flow_id: str, session_id: str, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: LcaRecorder(svc).get(actor, flow_id, session_id),
        )

    @router.post("/flows/{flow_id}/recordings/{session_id}/cancel", status_code=202)
    def cancel_recording(
        project_id: str, flow_id: str, session_id: str, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: LcaRecorder(svc).cancel(actor, flow_id, session_id),
        )

    @router.get("/flows/{flow_id}/runs")
    def list_runs(project_id: str, flow_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: [_public(row) for row in svc.list_runs(actor, flow_id)],
        )

    @router.post("/flows/{flow_id}/runs", status_code=202)
    def create_run(
        project_id: str, flow_id: str, body: RunInput, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(
                svc.debug_run(
                    actor, flow_id, body.expected_version, body.parameters, key(request)
                )
            ),
        )

    @router.get("/flows/{flow_id}/releases")
    def list_releases(project_id: str, flow_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: [
                _public(row) for row in svc.list_releases(actor, flow_id)
            ],
        )

    @router.post("/flows/{flow_id}/releases", status_code=201)
    def publish_release(
        project_id: str, flow_id: str, body: PublishInput, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(
                svc.publish_release(
                    actor,
                    flow_id,
                    body.expected_version,
                    body.verified_run_id,
                    key(request),
                )
            ),
        )

    @router.get("/releases/{release_id}")
    def get_release(project_id: str, release_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(svc.get_release(actor, release_id)),
        )

    @router.post("/releases/{release_id}/runs", status_code=202)
    def run_release(
        project_id: str, release_id: str, body: ReleaseRunInput, request: Request
    ) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(
                svc.run_release(actor, release_id, body.parameters, key(request))
            ),
        )

    @router.get("/runs/{run_id}")
    def get_run(project_id: str, run_id: str, request: Request) -> Any:
        return invoke(
            request, project_id, lambda svc, actor: _public(svc.get_run(actor, run_id))
        )

    @router.post("/runs/{run_id}/reconcile", status_code=202)
    def reconcile_run(project_id: str, run_id: str, request: Request) -> Any:
        return invoke(
            request,
            project_id,
            lambda svc, actor: _public(svc.reconcile_run(actor, run_id)),
        )

    @router.post("/runs/{run_id}/cancel", status_code=202)
    def cancel_run(project_id: str, run_id: str, request: Request) -> Any:
        return invoke(
            request, project_id, lambda svc, actor: svc.cancel_run(actor, run_id)
        )

    @router.get("/runs/{run_id}/evidence/{evidence_id}")
    def evidence(
        project_id: str, run_id: str, evidence_id: str, request: Request
    ) -> Response:
        row, data = invoke(
            request,
            project_id,
            lambda svc, actor: svc.read_evidence(actor, run_id, evidence_id),
        )
        mime = {
            "screenshot": "image/png",
            "video": "video/webm",
            "trace": "application/zip",
            "result": "application/json",
            "download": "application/octet-stream",
        }[row["kind"]]
        headers = {
            "X-Content-SHA256": row["sha256"],
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if row["kind"] == "download":
            headers["Content-Disposition"] = (
                f'attachment; filename="{safe_filename(row.get("filename", "download-" + row["id"] + ".bin"))}"'
            )
        return Response(data, media_type=mime, headers=headers)

    return router


class DelegatedLcaAuthorizer:
    """Accept actor delegation only from the separately authenticated gateway."""

    def __init__(self, token: str, project_id: str) -> None:
        if len(token) < 16 or not project_id:
            raise ValueError("LCA delegation requires a server credential and project")
        self._token = token
        self._project = project_id

    def __call__(self, request: Request, project_id: str) -> AccessPrincipal:
        presented = request.headers.get("authorization", "")
        if (
            project_id != self._project
            or not presented.startswith("Bearer ")
            or not secrets.compare_digest(presented[7:], self._token)
        ):
            raise PermissionError("LCA gateway authorization required")
        actor = request.headers.get("x-lca-actor", "")
        try:
            return AccessPrincipal(
                project_id=project_id,
                principal_id=actor,
                principal_type="user",
                audience="tap-lca",
                expires_at=datetime.now(UTC) + timedelta(seconds=60),
                enabled=True,
                actions=frozenset(
                    {
                        "lca.assets.create",
                        "lca.assets.read",
                        "lca.releases.publish",
                        "lca.releases.read",
                        "lca.flows.create",
                        "lca.flows.read",
                        "lca.drafts.save",
                        "lca.drafts.generate",
                        "lca.runs.create",
                        "lca.runs.read",
                        "lca.runs.cancel",
                        "lca.evidence.read",
                        "lca.recordings.create",
                        "lca.recordings.read",
                        "lca.recordings.cancel",
                    }
                ),
            )
        except (ValueError, TypeError):
            raise PermissionError("Invalid delegated LCA actor") from None
