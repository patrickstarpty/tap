"""HTTP contract for the Skill import, authoring and read surface."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.authorization import AuthorizationDecision
from tap.modules.ai.application.skill_authoring import SkillAuthoringService
from tap.modules.ai.application.skill_package import SkillPackageRejected
from tap.modules.ai.domain.assets import (
    AssetRevisionStatus,
    RevisionConflict,
    SkillRevision,
    text_digest,
)
from tap.platform.storage.objects import (
    ObjectMissingError,
    ObjectRef,
    StagedObject,
    StagingRef,
    VerifiedObject,
)


class _FakeCatalog:
    """Backs both read (`AssetCatalogHttpService`) and write (`SkillAuthoringCatalog`) ports."""

    def __init__(self) -> None:
        self.scope = VALIDATION_SCOPE
        self._skills: dict[str, list[SkillRevision]] = {}
        self._file_refs: dict[tuple[str, str], str] = {}
        self._imports: dict[str, dict[str, object]] = {}

    # -- read: AssetCatalogHttpService ----------------------------------

    async def list_agents(self, scope):
        return ()

    async def get_agent(self, scope, revision_id):
        raise LookupError(revision_id)

    async def list_skills(self, scope):
        # One row per asset -- its latest revision, any status (C-1/C-2).
        return tuple(history[-1] for history in self._skills.values() if history)

    async def get_skill(self, scope, revision_id):
        from tap.modules.ai.domain.assets import AssetRevisionRejected

        for history in self._skills.values():
            for revision in history:
                if revision.revision_id == revision_id and revision.status == (
                    AssetRevisionStatus.ENABLED
                ):
                    return revision
        raise AssetRevisionRejected()

    async def resolve_historical_skill(self, scope, revision_id):
        from tap.modules.ai.domain.assets import AssetRevisionRejected

        for history in self._skills.values():
            for revision in history:
                if revision.revision_id == revision_id:
                    return revision
        raise AssetRevisionRejected()

    async def list_revisions(self, kind, asset_id):
        if kind != "skill":
            return ()
        return tuple(self._skills.get(asset_id, ()))

    # -- write: SkillAuthoringCatalog ------------------------------------

    async def insert_skill_revision(self, revision, *, base_revision_id, file_object_refs=None):
        history = self._skills.setdefault(revision.asset_id, [])
        latest = history[-1].revision_id if history else None
        if latest != base_revision_id:
            raise RevisionConflict()
        history.append(revision)
        for file in revision.files:
            ref = (file_object_refs or {}).get(file.relative_path)
            if ref is not None:
                self._file_refs[(revision.revision_id, file.relative_path)] = ref
        return revision

    async def set_asset_enabled(self, kind, asset_id, enabled):
        from dataclasses import replace

        from tap.modules.ai.domain.assets import AssetRevisionRejected

        history = self._skills.get(asset_id)
        if not history:
            raise AssetRevisionRejected()
        status = AssetRevisionStatus.ENABLED if enabled else AssetRevisionStatus.DISABLED
        self._skills[asset_id] = [replace(revision, status=status) for revision in history]

    async def find_skill_by_name(self, scope, name):
        for history in self._skills.values():
            if history and history[-1].display_name == name:
                return history[-1]
        return None

    async def get_skill_file_object_ref(self, revision_id, relative_path):
        return self._file_refs.get((revision_id, relative_path))

    async def create_skill_import(self, *, import_id, preview_json, staging_locators, expires_at):
        self._imports[import_id] = {
            "preview_json": preview_json,
            "staging_locators": tuple(staging_locators),
            "expires_at": expires_at,
            "committed_result_json": None,
        }

    async def get_skill_import(self, import_id):
        from tap.modules.ai.application.skill_authoring import SkillImportRecord

        row = self._imports.get(import_id)
        if row is None:
            return None
        return SkillImportRecord(import_id=import_id, **row)

    async def set_skill_import_committed(self, import_id, result_json):
        row = self._imports.get(import_id)
        if row is None or row["committed_result_json"] is not None:
            return False
        row["committed_result_json"] = result_json
        return True

    async def purge_expired_skill_imports(self, *, now, limit):
        from tap.modules.ai.application.skill_authoring import SkillImportRecord

        expired = [
            SkillImportRecord(import_id=key, **value)
            for key, value in self._imports.items()
            if value["expires_at"] <= now
        ][:limit]
        for record in expired:
            del self._imports[record.import_id]
        return tuple(expired)


class _FakeObjectStore:
    def __init__(self) -> None:
        self.staged: dict[StagingRef, bytes] = {}
        self.objects: dict[ObjectRef, bytes] = {}
        self._counter = 0

    async def put_staged(self, request):
        import hashlib

        data = bytearray()
        async for chunk in request.content:
            data += chunk
        data = bytes(data)
        self._counter += 1
        ref = StagingRef(f"stg-{self._counter}")
        self.staged[ref] = data
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return StagedObject(ref, digest, len(data), request.content_type)

    async def promote(self, staged, expected_sha256, *, identity, attributes):
        import hashlib

        data = self.staged.pop(staged.ref, None)
        if data is None:
            raise ObjectMissingError("missing staged object")
        if f"sha256:{hashlib.sha256(data).hexdigest()}" != expected_sha256:
            raise ValueError("digest mismatch")
        self._counter += 1
        ref = ObjectRef(f"obj-{self._counter}")
        self.objects[ref] = data
        return ref

    async def open_verified(self, ref):
        import hashlib

        if isinstance(ref, StagingRef):
            data = self.staged.get(ref)
        else:
            data = self.objects.get(ref)
        if data is None:
            raise ObjectMissingError("missing object")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return VerifiedObject(data, digest, len(data), "application/octet-stream", str(ref), ())

    async def delete(self, ref):
        if ref in self.staged:
            del self.staged[ref]
            return
        if ref in self.objects:
            del self.objects[ref]
            return
        raise ObjectMissingError("missing object")


class _Authority:
    def __init__(self, allowed: frozenset[str] | None = None) -> None:
        self._allowed = allowed or frozenset({"ai.skills.read", "ai.skills.write"})

    async def current(self, _facts):
        return VALIDATION_SCOPE

    async def authorize(self, _scope, action, _resource):
        if action not in self._allowed:
            return AuthorizationDecision(False, "action-not-allowed")
        return AuthorizationDecision(True, "validation-allowed")


def _clock_box(now: datetime | None = None) -> list[datetime]:
    return [now or datetime(2026, 1, 1, tzinfo=timezone.utc)]


ORIGIN = "http://127.0.0.1:15175"


def _app(
    *, allowed: frozenset[str] | None = None, clock_box: list[datetime] | None = None
) -> tuple[TestClient, _FakeCatalog]:
    catalog = _FakeCatalog()
    objects = _FakeObjectStore()
    box = clock_box if clock_box is not None else _clock_box()
    skill_authoring = SkillAuthoringService(
        catalog, objects, scope=VALIDATION_SCOPE, clock=lambda: box[0]
    )
    authority = _Authority(allowed)
    app = create_app(
        HttpServices(
            asset_catalog=catalog,
            skill_authoring=skill_authoring,
            scope=VALIDATION_SCOPE,
            scope_provider=authority,
            authorization_policy=authority,
        ),
        allowed_origins=frozenset({ORIGIN}),
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    return client, catalog


BASE = "/api/v1/projects/tapper-demo/ai"


def _create_skill(client: TestClient, name: str = "demo-skill") -> dict:
    response = client.post(
        f"{BASE}/skills",
        json={
            "name": name,
            "description": "A demo skill for contract tests.",
            "instructions": "Do the demo thing.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_built_in_skill(
    catalog: "_FakeCatalog", *, skill_id: str = "builtin-skill", name: str = "Built-in skill"
) -> SkillRevision:
    # I-4: built-in Skills are seeded server-side (validation_asset_seed),
    # never created through the authoring HTTP surface -- write directly
    # into the fake catalog's store to set up a built-in asset the way the
    # real seeded catalog would exist.
    body = "Built-in instructions."
    revision_id = f"{skill_id}-v1"
    revision = SkillRevision(
        revision_id=revision_id,
        asset_id=skill_id,
        display_name=name,
        scope=VALIDATION_SCOPE,
        content_digest=text_digest(revision_id),
        instruction_template_digest=text_digest(body),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template=body,
        source_kind="built_in",
    )
    catalog._skills[skill_id] = [revision]
    return revision


# -- POST /skills (create) -----------------------------------------------


def test_create_skill_returns_authored_revision_detail() -> None:
    client, _catalog = _app()

    body = _create_skill(client)

    assert body["sourceKind"] == "authored"
    assert body["builtIn"] is False
    assert body["status"] == "enabled"
    assert body["isLatest"] is True
    assert body["skillId"] == body["assetId"]
    assert body["instructions"].strip() == "Do the demo thing."
    assert len(body["files"]) == 1
    file_view = body["files"][0]
    assert file_view["relativePath"] == "SKILL.md"
    assert file_view["category"] == "skill"
    assert file_view["attached"] is False
    assert file_view["sizeBytes"] > 0
    assert len(file_view["sha256"]) == 64


def test_create_skill_requires_write_permission() -> None:
    client, _catalog = _app(allowed=frozenset({"ai.skills.read"}))

    response = client.post(
        f"{BASE}/skills",
        json={"name": "demo-skill", "description": "demo", "instructions": "do it"},
    )

    assert response.status_code == 403


# -- POST /skills/{skillId}/revisions ------------------------------------


def test_revise_skill_requires_matching_base_revision() -> None:
    client, _catalog = _app()
    created = _create_skill(client)

    stale = client.post(
        f"{BASE}/skills/{created['skillId']}/revisions",
        json={
            "name": "demo-skill",
            "description": "v2",
            "instructions": "v2 body",
            "baseRevisionId": "not-the-real-base",
        },
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "revision_conflict",
        "detail": "the requested skill revision conflicts with the current revision",
    }

    revised = client.post(
        f"{BASE}/skills/{created['skillId']}/revisions",
        json={
            "name": "demo-skill",
            "description": "v2",
            "instructions": "v2 body",
            "baseRevisionId": created["revisionId"],
        },
    )
    assert revised.status_code == 201
    assert revised.json()["revisionId"] != created["revisionId"]
    assert revised.json()["isLatest"] is True


# -- POST /skills/{skillId}/disable, /enable -----------------------------


def test_disable_then_enable_toggles_status() -> None:
    client, _catalog = _app()
    created = _create_skill(client)
    skill_id = created["skillId"]

    disabled = client.post(f"{BASE}/skills/{skill_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = client.post(f"{BASE}/skills/{skill_id}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"


# -- I-4: built-in Skills are read-only -----------------------------------


def test_revise_built_in_skill_is_rejected() -> None:
    client, catalog = _app()
    builtin = _seed_built_in_skill(catalog)

    response = client.post(
        f"{BASE}/skills/{builtin.asset_id}/revisions",
        json={
            "name": "built-in-skill",
            "description": "v2",
            "instructions": "v2 body",
            "baseRevisionId": builtin.revision_id,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "skill_built_in_immutable"


def test_disable_built_in_skill_is_rejected() -> None:
    client, catalog = _app()
    builtin = _seed_built_in_skill(catalog)

    response = client.post(f"{BASE}/skills/{builtin.asset_id}/disable")

    assert response.status_code == 403
    assert response.json()["code"] == "skill_built_in_immutable"


def test_enable_built_in_skill_is_rejected() -> None:
    client, catalog = _app()
    builtin = _seed_built_in_skill(catalog)

    response = client.post(f"{BASE}/skills/{builtin.asset_id}/enable")

    assert response.status_code == 403
    assert response.json()["code"] == "skill_built_in_immutable"


def test_commit_import_as_new_revision_of_a_built_in_skill_is_rejected() -> None:
    client, catalog = _app()
    builtin = _seed_built_in_skill(catalog)
    text = "---\nname: imported-over-builtin\ndescription: d.\n---\n\nbody\n"
    preview = client.post(f"{BASE}/skills/imports", json={"text": text}).json()

    response = client.post(
        f"{BASE}/skills/imports/{preview['importId']}/commit",
        json={
            "selections": [
                {
                    "root": preview["skills"][0]["root"],
                    "asNewRevisionOf": builtin.asset_id,
                }
            ]
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "skill_built_in_immutable"


# -- GET /skills, /skills/{revisionId}, /skills/{skillId}/revisions -----


def test_list_skills_keeps_legacy_fields_and_adds_new_ones() -> None:
    client, _catalog = _app()
    created = _create_skill(client)

    listed = client.get(f"{BASE}/skills")
    assert listed.status_code == 200
    (item,) = listed.json()["items"]
    legacy_fields = ("revisionId", "assetId", "displayName", "contentDigest", "applicableTasks")
    for legacy_field in legacy_fields:
        assert legacy_field in item
    for new_field in ("description", "sourceKind", "builtIn", "status", "isLatest", "skillId"):
        assert new_field in item
    assert item["revisionId"] == created["revisionId"]
    assert item["isLatest"] is True


def test_get_skill_detail_includes_files_and_instructions() -> None:
    client, _catalog = _app()
    created = _create_skill(client)

    detail = client.get(f"{BASE}/skills/{created['revisionId']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["instructions"].strip() == "Do the demo thing."
    assert body["files"][0]["relativePath"] == "SKILL.md"


def test_revision_history_marks_latest_revision() -> None:
    client, _catalog = _app()
    created = _create_skill(client)
    client.post(
        f"{BASE}/skills/{created['skillId']}/revisions",
        json={
            "name": "demo-skill",
            "description": "v2",
            "instructions": "v2 body",
            "baseRevisionId": created["revisionId"],
        },
    )

    history = client.get(f"{BASE}/skills/{created['skillId']}/revisions")
    assert history.status_code == 200
    items = history.json()["items"]
    assert len(items) == 2
    assert items[0]["isLatest"] is False
    assert items[1]["isLatest"] is True


def test_revision_history_unknown_skill_is_not_found() -> None:
    client, _catalog = _app()

    response = client.get(f"{BASE}/skills/unknown-skill/revisions")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


# -- GET /skills/{revisionId}/files/{path} -------------------------------


def test_download_skill_file_sets_attachment_and_text_content_type() -> None:
    client, _catalog = _app()
    created = _create_skill(client)

    response = client.get(f"{BASE}/skills/{created['revisionId']}/files/SKILL.md")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["content-type"] in {
        "text/plain; charset=utf-8",
        "application/octet-stream",
    }


def test_download_unknown_file_path_rejected() -> None:
    client, _catalog = _app()
    created = _create_skill(client)

    response = client.get(f"{BASE}/skills/{created['revisionId']}/files/unknown.txt")

    assert response.status_code == 400
    assert response.json() == {
        "code": "skill_path_rejected",
        "detail": "path is not part of this revision's file table",
    }


# -- POST /skills/imports -------------------------------------------------


def test_import_skill_via_json_text_then_commit() -> None:
    client, _catalog = _app()
    text = (
        "---\nname: text-skill\ndescription: Imported from pasted text.\n---\n\nText skill body.\n"
    )

    preview = client.post(f"{BASE}/skills/imports", json={"text": text})
    assert preview.status_code == 201
    body = preview.json()
    assert len(body["skills"]) == 1
    assert body["skills"][0]["name"] == "text-skill"
    assert body["skills"][0]["conflictSkillId"] is None

    commit = client.post(
        f"{BASE}/skills/imports/{body['importId']}/commit",
        json={"selections": [{"root": body["skills"][0]["root"]}]},
    )
    assert commit.status_code == 200
    (revision,) = commit.json()["revisions"]
    assert revision["sourceKind"] == "imported"
    assert revision["displayName"] == "text-skill"


def test_import_skill_via_multipart_files() -> None:
    client, _catalog = _app()
    skill_md = (
        b"---\nname: files-skill\ndescription: Imported from files upload.\n---\n\nFiles body.\n"
    )

    response = client.post(
        f"{BASE}/skills/imports",
        files={"files": ("files-skill/SKILL.md", io.BytesIO(skill_md), "text/markdown")},
    )

    assert response.status_code == 201
    assert response.json()["skills"][0]["name"] == "files-skill"


def test_import_skill_multipart_duplicate_filenames_rejected() -> None:
    # A silent dict-key overwrite would drop one of the two uploaded files
    # without any signal to the caller.
    client, _catalog = _app()
    first = b"first bytes"
    second = b"second bytes, different content"

    response = client.post(
        f"{BASE}/skills/imports",
        files=[
            ("files", ("demo-skill/SKILL.md", io.BytesIO(first), "text/markdown")),
            ("files", ("demo-skill/SKILL.md", io.BytesIO(second), "text/markdown")),
        ],
    )

    assert response.status_code == 422


def test_import_skill_archive_too_large_rejected() -> None:
    client, _catalog = _app()
    oversized = b"0" * (20 * 1024 * 1024 + 1)

    response = client.post(
        f"{BASE}/skills/imports",
        files={"archive": ("skill.zip", io.BytesIO(oversized), "application/zip")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "code": "skill_package_too_large",
        "detail": "the uploaded skill package exceeds the archive size limit",
    }


def test_import_skill_many_files_aggregate_too_large_rejected() -> None:
    client, _catalog = _app()
    chunk = b"0" * (3 * 1024 * 1024)  # 3 MiB per file, well under the 20 MiB bound alone
    files = [
        (
            "files",
            (f"demo-skill/part-{index}.bin", io.BytesIO(chunk), "application/octet-stream"),
        )
        for index in range(8)  # 24 MiB aggregate across parts
    ]

    response = client.post(f"{BASE}/skills/imports", files=files)

    assert response.status_code == 413
    assert response.json()["code"] == "skill_package_too_large"


def test_import_skill_requires_an_upload_or_text_body() -> None:
    client, _catalog = _app()

    response = client.post(f"{BASE}/skills/imports", json={})

    assert response.status_code == 422


def test_import_frontmatter_invalid_rejected() -> None:
    client, _catalog = _app()
    bad_text = "---\nname: Not A Valid Name\ndescription: bad.\n---\n\nBody.\n"

    response = client.post(f"{BASE}/skills/imports", json={"text": bad_text})

    assert response.status_code == 400
    assert response.json()["code"] == "skill_frontmatter_invalid"


def test_commit_unknown_root_is_skill_not_found_in_package() -> None:
    client, _catalog = _app()
    text = "---\nname: root-skill\ndescription: d.\n---\n\nbody\n"
    preview = client.post(f"{BASE}/skills/imports", json={"text": text}).json()

    response = client.post(
        f"{BASE}/skills/imports/{preview['importId']}/commit",
        json={"selections": [{"root": "not-the-real-root"}]},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "skill_not_found_in_package"


def test_commit_name_conflict_requires_as_new_revision_of() -> None:
    client, _catalog = _app()
    text = "---\nname: dup-skill\ndescription: d.\n---\n\nbody\n"
    first_preview = client.post(f"{BASE}/skills/imports", json={"text": text}).json()
    client.post(
        f"{BASE}/skills/imports/{first_preview['importId']}/commit",
        json={"selections": [{"root": first_preview["skills"][0]["root"]}]},
    )

    second_preview = client.post(f"{BASE}/skills/imports", json={"text": text}).json()
    assert second_preview["skills"][0]["conflictSkillId"] is not None

    conflict = client.post(
        f"{BASE}/skills/imports/{second_preview['importId']}/commit",
        json={"selections": [{"root": second_preview["skills"][0]["root"]}]},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "skill_name_conflict"


def test_commit_after_staging_expired() -> None:
    box = _clock_box()
    client, _catalog = _app(clock_box=box)
    text = "---\nname: expiring-skill\ndescription: d.\n---\n\nbody\n"
    preview = client.post(f"{BASE}/skills/imports", json={"text": text}).json()

    box[0] = box[0] + timedelta(hours=25)

    response = client.post(
        f"{BASE}/skills/imports/{preview['importId']}/commit",
        json={"selections": [{"root": preview["skills"][0]["root"]}]},
    )

    assert response.status_code == 410
    assert response.json()["code"] == "import_expired"


def test_import_requires_write_permission() -> None:
    client, _catalog = _app(allowed=frozenset({"ai.skills.read"}))

    response = client.post(
        f"{BASE}/skills/imports",
        json={"text": "---\nname: x\ndescription: d\n---\nbody"},
    )

    assert response.status_code == 403


# -- synthetic coverage of every registered skill error code ------------


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("skill_package_too_large", 413),
        ("skill_path_rejected", 400),
        ("skill_frontmatter_invalid", 400),
        ("skill_not_found_in_package", 400),
        ("skill_name_conflict", 409),
        ("revision_conflict", 409),
        ("import_expired", 410),
        ("skill_budget_exceeded", 400),
    ],
)
def test_every_registered_skill_error_code_maps_to_its_status(
    code: str, expected_status: int
) -> None:
    client, _catalog = _app()

    async def fail() -> None:
        raise SkillPackageRejected(code, f"{code} detail")

    client.app.add_api_route(f"/_test/skill-problem/{code}", fail, methods=["GET"])
    response = client.get(f"/_test/skill-problem/{code}")

    assert response.status_code == expected_status
    assert response.json() == {"code": code, "detail": f"{code} detail"}
