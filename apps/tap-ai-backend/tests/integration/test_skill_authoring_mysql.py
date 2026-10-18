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

        data = self.staged.pop(staged.ref, None)
        if data is None:
            raise ObjectMissingError("missing staged object")
        self._counter += 1
        ref = ObjectRef(f"obj-{self._counter}")
        self.objects[ref] = data
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
        self.objects.pop(ref, None)


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
