"""SkillAuthoringService backed by the real MySQL approved catalog.

Run with TAP_RUN_MYSQL_INTEGRATION=1 against an isolated schema. The object
store side stays an in-memory double (per plan) so this test only exercises
real MySQL persistence: skill/skill_revision/skill_revision_file/skill_import
rows, idempotent commit, and name-conflict detection across a real database
round trip.
"""

from __future__ import annotations

import hashlib

import pytest

_SKILL_MD = (
    "---\n"
    "name: demo-skill\n"
    "description: Demo skill used in the MySQL round trip test.\n"
    "---\n"
    "\n"
    "Use the demo skill body as instructions.\n"
)


class _FakeObjectStore:
    def __init__(self) -> None:
        self.staged: dict[object, bytes] = {}
        self.objects: dict[object, bytes] = {}
        self._promoted: dict[tuple[object, ...], object] = {}
        self._counter = 0

    async def put_staged(self, request):
        from tap.platform.storage.objects import StagedObject, StagingRef

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
        from tap.platform.storage.objects import ObjectMissingError, ObjectRef

        # Mirrors the real store: non-destructive of staging, and
        # content-addressed/idempotent (same identity+content -> same ref).
        data = self.staged.get(staged.ref)
        if data is None:
            raise ObjectMissingError("missing staged object")
        key = (identity, expected_sha256, len(data), staged.content_type)
        existing_ref = self._promoted.get(key)
        if existing_ref is not None:
            return existing_ref
        self._counter += 1
        ref = ObjectRef(f"obj-{self._counter}")
        self.objects[ref] = data
        self._promoted[key] = ref
        return ref

    async def open_verified(self, ref):
        from tap.platform.storage.objects import ObjectMissingError, VerifiedObject

        data = self.staged.get(ref) or self.objects.get(ref)
        if data is None:
            raise ObjectMissingError("missing object")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return VerifiedObject(data, digest, len(data), "application/octet-stream", "ref", ())

    async def delete(self, ref):
        self.staged.pop(ref, None)
        if self.objects.pop(ref, None) is not None:
            for key, promoted_ref in list(self._promoted.items()):
                if promoted_ref == ref:
                    del self._promoted[key]


@pytest.mark.asyncio
async def test_import_commit_round_trip(owned_project_mysql) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.skill_authoring import (
        FilesUpload,
        ImportSelection,
        SkillAuthoringService,
    )

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        objects = _FakeObjectStore()
        service = SkillAuthoringService(catalog, objects, scope=VALIDATION_SCOPE)

        upload = FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode("utf-8")})
        preview = await service.stage_import(VALIDATION_SCOPE, upload=upload)
        assert preview.skills[0].conflict_skill_id is None

        selections = (ImportSelection(root="demo-skill"),)
        (revision,) = await service.commit_import(VALIDATION_SCOPE, preview.import_id, selections)
        assert revision.source_kind == "imported"
        assert revision.display_name == "demo-skill"

        persisted = await catalog.list_revisions("skill", revision.asset_id)
        assert [item.revision_id for item in persisted] == [revision.revision_id]
        assert [file.relative_path for file in persisted[0].files] == ["SKILL.md"]

        # Idempotent: committing the same import_id again returns the same revision.
        (again,) = await service.commit_import(VALIDATION_SCOPE, preview.import_id, selections)
        assert again.revision_id == revision.revision_id
        assert len(await catalog.list_revisions("skill", revision.asset_id)) == 1

        # Opening the persisted file returns the original bytes via the object store.
        opened = await service.open_file(VALIDATION_SCOPE, revision.revision_id, "SKILL.md")
        assert opened.data == _SKILL_MD.encode("utf-8")

        # Re-staging the same skill name surfaces the conflict, and committing
        # without as_new_revision_of is rejected.
        second_preview = await service.stage_import(VALIDATION_SCOPE, upload=upload)
        assert second_preview.skills[0].conflict_skill_id == revision.asset_id

        from tap.modules.ai.application.skill_package import SkillPackageRejected

        with pytest.raises(SkillPackageRejected) as excinfo:
            await service.commit_import(VALIDATION_SCOPE, second_preview.import_id, selections)
        assert excinfo.value.code == "skill_name_conflict"

        (revised,) = await service.commit_import(
            VALIDATION_SCOPE,
            second_preview.import_id,
            (ImportSelection(root="demo-skill", as_new_revision_of=revision.asset_id),),
        )
        assert revised.asset_id == revision.asset_id
        assert revised.revision_id != revision.revision_id
        assert len(await catalog.list_revisions("skill", revision.asset_id)) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_file_and_unsafe_path_round_trip(owned_project_mysql) -> None:
    """I-4: a zero-byte file and a relative path containing characters a
    real object store's identity segments forbid (space, unicode, "+", "@")
    must both round-trip through the real MySQL file table."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.skill_authoring import (
        FilesUpload,
        ImportSelection,
        SkillAuthoringService,
    )

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        catalog = MysqlAssetCatalog(
            async_sessionmaker(engine, expire_on_commit=False), scope=VALIDATION_SCOPE
        )
        objects = _FakeObjectStore()
        service = SkillAuthoringService(catalog, objects, scope=VALIDATION_SCOPE)

        tricky_path = "tricky-skill/notes é+extra@home.txt"
        upload = FilesUpload(
            files={
                "tricky-skill/SKILL.md": (
                    "---\nname: tricky-skill\ndescription: Has tricky files.\n---\n\nBody.\n"
                ).encode("utf-8"),
                "tricky-skill/empty.txt": b"",
                tricky_path: b"tricky content",
            }
        )
        preview = await service.stage_import(VALIDATION_SCOPE, upload=upload)
        (revision,) = await service.commit_import(
            VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="tricky-skill"),)
        )

        empty_file = next(f for f in revision.files if f.relative_path == "empty.txt")
        assert empty_file.size_bytes == 0
        opened_empty = await service.open_file(VALIDATION_SCOPE, revision.revision_id, "empty.txt")
        assert opened_empty.data == b""

        opened_tricky = await service.open_file(
            VALIDATION_SCOPE, revision.revision_id, "notes é+extra@home.txt"
        )
        assert opened_tricky.data == b"tricky content"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skill_revision_and_event_commit_together(owned_project_mysql) -> None:
    """A maintenance event is written in the revision's own transaction: when
    the event insert fails, the revision is not stored either."""
    from dataclasses import replace
    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.maintenance import skill_maintenance_event
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog
    from tap.modules.ai.application.skill_authoring import SkillAuthoringService, SkillDraft
    from tap.modules.ai.domain.assets import RevisionConflict
    from tap.modules.ai.domain.maintenance import new_skill_event

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        service = SkillAuthoringService(catalog, _FakeObjectStore(), scope=VALIDATION_SCOPE)
        created = await service.create_authored(
            VALIDATION_SCOPE,
            SkillDraft(name="evented-skill", description="v1.", instructions="Body."),
        )
        revised = await service.revise(
            VALIDATION_SCOPE,
            created.asset_id,
            created.revision_id,
            SkillDraft(name="evented-skill", description="v2.", instructions="Body."),
        )
        async with sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(skill_maintenance_event).where(
                            skill_maintenance_event.c.skill_id == created.asset_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert [(row["kind"], row["revision_id"]) for row in rows] == [
            ("revised", revised.revision_id)
        ]
        assert rows[0]["actor_id"] == VALIDATION_SCOPE.actor_id

        # Reusing the stored event id makes the event insert fail.
        duplicate = replace(
            new_skill_event(
                VALIDATION_SCOPE,
                created.asset_id,
                "revised",
                revision_id="skill-rev-never-stored",
                now=datetime.now(timezone.utc),
            ),
            event_id=rows[0]["event_id"],
        )
        rejected = replace(
            revised,
            revision_id="skill-rev-never-stored",
            adopted_from_revision_id=revised.revision_id,
            files=(),
        )
        with pytest.raises(RevisionConflict):
            await catalog.insert_skill_revision(
                rejected, base_revision_id=revised.revision_id, skill_event=duplicate
            )
        history = await catalog.list_revisions("skill", created.asset_id)
        assert [item.revision_id for item in history] == [
            created.revision_id,
            revised.revision_id,
        ]

        # Toggling writes `disabled` once; a repeat toggle writes nothing.
        await service.set_enabled(VALIDATION_SCOPE, created.asset_id, False)
        await service.set_enabled(VALIDATION_SCOPE, created.asset_id, False)
        async with sessions() as session:
            kinds = (
                await session.scalars(
                    select(skill_maintenance_event.c.kind)
                    .where(skill_maintenance_event.c.skill_id == created.asset_id)
                    .order_by(skill_maintenance_event.c.occurred_at)
                )
            ).all()
        assert kinds == ["revised", "disabled"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_skill_owner_defaults_to_creator(owned_project_mysql) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.mysql import MysqlAssetCatalog, skill, skill_revision
    from tap.modules.ai.application.skill_authoring import (
        ImportSelection,
        SkillAuthoringService,
        TextUpload,
    )

    engine = create_async_engine(owned_project_mysql.url.replace("mysql+pymysql", "mysql+asyncmy"))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        catalog = MysqlAssetCatalog(sessions, scope=VALIDATION_SCOPE)
        service = SkillAuthoringService(catalog, _FakeObjectStore(), scope=VALIDATION_SCOPE)
        text = (
            "---\nname: owned-skill\ndescription: d.\n"
            "metadata:\n  category: knowledge\n  version: '0.3.0'\n---\n\nBody.\n"
        )
        preview = await service.stage_import(VALIDATION_SCOPE, upload=TextUpload(text=text))
        (revision,) = await service.commit_import(
            VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="", category="writing"),)
        )

        async with sessions() as session:
            owner = (
                (await session.execute(select(skill).where(skill.c.skill_id == revision.asset_id)))
                .mappings()
                .one()
            )
            stored = (
                (
                    await session.execute(
                        select(skill_revision).where(
                            skill_revision.c.revision_id == revision.revision_id
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert owner["owner_actor_id"] == VALIDATION_SCOPE.actor_id
        assert owner["owner_assigned_at"] is not None
        assert owner["category"] == "knowledge"
        assert stored["version_label"] == "0.3.0"
        assert stored["origin_kind"] == "paste"
        assert stored["origin_ref"] is None
        assert stored["origin_imported_at"] is not None

        persisted = next(
            item
            for item in await catalog.list_skills(VALIDATION_SCOPE)
            if item.asset_id == revision.asset_id
        )
        assert persisted.category == "knowledge"
        assert persisted.version_label == "0.3.0"
        assert persisted.origin_kind == "paste"
    finally:
        await engine.dispose()
