"""SkillAuthoringService: import, author, revise, disable, download.

Uses in-memory doubles for the catalog port and the object store so these
tests exercise only the service's own orchestration logic; MySQL-backed
behavior is covered separately in the isolated integration test.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.skill_authoring import (
    FilesUpload,
    ImportSelection,
    SkillAuthoringService,
    SkillDraft,
    SkillImportRecord,
    TextUpload,
    _deterministic_id,
    _file_identity,
)
from tap.modules.ai.application.skill_package import SkillPackageRejected
from tap.modules.ai.domain.assets import (
    AssetRevisionRejected,
    AssetRevisionStatus,
    RevisionConflict,
    SkillFile,
    SkillRevision,
    text_digest,
)
from tap.platform.storage.objects import (
    ObjectIntegrityError,
    ObjectMissingError,
    ObjectRef,
    PutObjectRequest,
    StagedObject,
    StagingRef,
    VerifiedObject,
)

# Mirrors the real S3ObjectStore's identity-segment restriction so a test
# package with a path outside it (space, unicode, leading dot, "+", "@")
# exercises the same rejection the real provider would raise.
_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,255}\Z")

_SKILL_MD = (
    "---\n"
    "name: demo-skill\n"
    "description: Demo skill used in authoring tests.\n"
    "---\n"
    "\n"
    "Use the demo skill body as instructions.\n"
)


class _FakeCatalog:
    def __init__(self, *, fail_inserts: int = 0) -> None:
        self._skills: dict[str, list[SkillRevision]] = {}
        self._file_refs: dict[tuple[str, str], str] = {}
        self._imports: dict[str, dict[str, object]] = {}
        # Test hook for M-5: fail this many inserts (counting down) with a
        # plain RuntimeError, simulating a DB write failure unrelated to any
        # revision conflict.
        self._fail_inserts = fail_inserts

    async def insert_skill_revision(self, revision, *, base_revision_id, file_object_refs=None):
        if self._fail_inserts > 0:
            self._fail_inserts -= 1
            raise RuntimeError("simulated database failure")
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
        history = self._skills.get(asset_id)
        if not history:
            raise LookupError(asset_id)
        status = AssetRevisionStatus.ENABLED if enabled else AssetRevisionStatus.DISABLED
        history[-1] = replace(history[-1], status=status)

    async def list_revisions(self, kind, asset_id):
        return tuple(self._skills.get(asset_id, ()))

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
        self.objects: dict[ObjectRef, tuple[bytes, str, dict[str, str], str]] = {}
        self._promoted: dict[tuple[object, ...], ObjectRef] = {}
        self._counter = 0

    async def put_staged(self, request):
        data = bytearray()
        async for chunk in request.content:
            data += chunk
        data = bytes(data)
        if not data:
            # Mirrors the real S3ObjectStore, which rejects empty uploads:
            # callers must never stage a zero-byte file.
            raise ObjectIntegrityError("object upload is empty")
        self._counter += 1
        ref = StagingRef(f"stg-{self._counter}")
        self.staged[ref] = data
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        return StagedObject(ref, digest, len(data), request.content_type)

    async def promote(self, staged, expected_sha256, *, identity, attributes):
        if not isinstance(identity, str) or len(identity) > 512:
            raise ValueError("invalid identity")
        for part in identity.split("/"):
            if _SEGMENT.fullmatch(part) is None or part in (".", ".."):
                raise ValueError(f"invalid identity segment: {part!r}")
        # Mirrors the real store: promote() reads the staged bytes but never
        # consumes/deletes the staging entry itself.
        data = self.staged.get(staged.ref)
        if data is None:
            raise ObjectMissingError("missing staged object")
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if digest != expected_sha256:
            raise ValueError("digest mismatch")
        # Content-addressed and idempotent, like the real store: the same
        # (identity, content, attributes) always resolves to the same ref,
        # so two concurrent committers promoting the same file converge on
        # one object rather than each creating their own.
        key = (identity, digest, len(data), staged.content_type, tuple(sorted(attributes.items())))
        existing_ref = self._promoted.get(key)
        if existing_ref is not None:
            return existing_ref
        self._counter += 1
        ref = ObjectRef(f"obj-{self._counter}")
        self.objects[ref] = (data, digest, dict(attributes), identity)
        self._promoted[key] = ref
        return ref

    async def open_verified(self, ref):
        if isinstance(ref, StagingRef):
            data = self.staged.get(ref)
            if data is None:
                raise ObjectMissingError("missing staged object")
            digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
            return VerifiedObject(data, digest, len(data), "application/octet-stream", "staged", ())
        entry = self.objects.get(ref)
        if entry is None:
            raise ObjectMissingError("missing object")
        data, digest, attributes, identity = entry
        return VerifiedObject(
            data,
            digest,
            len(data),
            "application/octet-stream",
            identity,
            tuple(sorted(attributes.items())),
        )

    async def delete(self, ref):
        if ref in self.staged:
            del self.staged[ref]
            return
        if ref in self.objects:
            del self.objects[ref]
            for key, promoted_ref in list(self._promoted.items()):
                if promoted_ref == ref:
                    del self._promoted[key]
            return
        raise ObjectMissingError("missing object")


def _service(catalog=None, objects=None, now=None):
    catalog = catalog if catalog is not None else _FakeCatalog()
    objects = objects if objects is not None else _FakeObjectStore()
    box = [now or datetime(2026, 1, 1, tzinfo=timezone.utc)]
    service = SkillAuthoringService(catalog, objects, scope=VALIDATION_SCOPE, clock=lambda: box[0])
    return service, catalog, objects, box


async def _stage_bytes_for_test(objects, data: bytes):
    async def content():
        yield data

    # Matches SkillAuthoringService._stage_bytes's fixed content type, so a
    # manually-staged-and-promoted "winner" collides with the service's own
    # promotion of the same (identity, content) under the idempotent fake
    # store's dedup key.
    return await objects.put_staged(
        PutObjectRequest(content(), max(len(data), 1), "application/octet-stream")
    )


@pytest.mark.asyncio
async def test_stage_then_commit_creates_revision_and_objects() -> None:
    service, catalog, objects, _ = _service()
    upload = FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode("utf-8")})

    preview = await service.stage_import(VALIDATION_SCOPE, upload=upload)

    assert len(preview.skills) == 1
    skill_preview = preview.skills[0]
    assert skill_preview.root == "demo-skill"
    assert skill_preview.name == "demo-skill"
    assert skill_preview.conflict_skill_id is None
    assert [file.relative_path for file in skill_preview.files] == ["SKILL.md"]
    assert objects.staged  # the file bytes are staged before commit

    revisions = await service.commit_import(
        VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
    )

    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.source_kind == "imported"
    assert revision.display_name == "demo-skill"
    assert revision.instruction_template is not None
    assert [file.relative_path for file in revision.files] == ["SKILL.md"]
    # Promoted into permanent storage (promote() does not consume staging,
    # matching the real store: staging cleanup is a separate concern).
    assert objects.objects

    ref = await catalog.get_skill_file_object_ref(revision.revision_id, "SKILL.md")
    assert ref is not None
    opened = await objects.open_verified(ObjectRef(ref))
    assert opened.data == _SKILL_MD.encode("utf-8")


@pytest.mark.asyncio
async def test_commit_is_idempotent() -> None:
    service, catalog, _, _ = _service()
    upload = FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode("utf-8")})
    preview = await service.stage_import(VALIDATION_SCOPE, upload=upload)
    selections = (ImportSelection(root="demo-skill"),)

    first = await service.commit_import(VALIDATION_SCOPE, preview.import_id, selections)
    second = await service.commit_import(VALIDATION_SCOPE, preview.import_id, selections)

    assert [r.revision_id for r in first] == [r.revision_id for r in second]
    assert len(catalog._skills[first[0].asset_id]) == 1


@pytest.mark.asyncio
async def test_name_conflict_requires_explicit_new_revision() -> None:
    service, _, _, _ = _service()
    first_import = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )
    (created,) = await service.commit_import(
        VALIDATION_SCOPE, first_import.import_id, (ImportSelection(root="demo-skill"),)
    )

    second_import = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )
    assert second_import.skills[0].conflict_skill_id == created.asset_id

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(
            VALIDATION_SCOPE, second_import.import_id, (ImportSelection(root="demo-skill"),)
        )
    assert excinfo.value.code == "skill_name_conflict"

    (revised,) = await service.commit_import(
        VALIDATION_SCOPE,
        second_import.import_id,
        (ImportSelection(root="demo-skill", as_new_revision_of=created.asset_id),),
    )
    assert revised.asset_id == created.asset_id
    assert revised.revision_id != created.revision_id


@pytest.mark.asyncio
async def test_expired_import_rejected() -> None:
    service, _, _, box = _service()
    preview = await service.stage_import(VALIDATION_SCOPE, upload=TextUpload(text=_SKILL_MD))
    box[0] = box[0] + timedelta(hours=25)

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(
            VALIDATION_SCOPE, preview.import_id, (ImportSelection(root=""),)
        )
    assert excinfo.value.code == "import_expired"

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(VALIDATION_SCOPE, "unknown-import-id", ())
    assert excinfo.value.code == "import_expired"


@pytest.mark.asyncio
async def test_authored_skill_matches_imported_shape() -> None:
    service, _, objects, _ = _service()
    draft = SkillDraft(
        name="authored-skill",
        description="An authored skill for shape comparison.",
        instructions="Follow the authored instructions.",
    )

    revision = await service.create_authored(VALIDATION_SCOPE, draft)

    assert revision.source_kind == "authored"
    assert revision.display_name == "authored-skill"
    assert revision.applicable_tasks == frozenset({"knowledge.answer"})
    assert [file.relative_path for file in revision.files] == ["SKILL.md"]
    assert revision.frontmatter is not None
    assert revision.frontmatter["name"] == "authored-skill"
    file_ref = revision.files[0]
    assert file_ref.category == "skill"
    opened = await objects.open_verified(ObjectRef(f"obj-{objects._counter}"))
    assert b"Follow the authored instructions." in opened.data


@pytest.mark.asyncio
async def test_stale_base_revision_conflicts() -> None:
    service, _, _, _ = _service()
    draft = SkillDraft(
        name="revisable-skill", description="First version.", instructions="V1 body."
    )
    first = await service.create_authored(VALIDATION_SCOPE, draft)
    revised_draft = SkillDraft(
        name="revisable-skill", description="Second version.", instructions="V2 body."
    )

    with pytest.raises(RevisionConflict):
        await service.revise(VALIDATION_SCOPE, first.asset_id, "not-the-real-base", revised_draft)

    second = await service.revise(
        VALIDATION_SCOPE, first.asset_id, first.revision_id, revised_draft
    )
    assert second.adopted_from_revision_id == first.revision_id


@pytest.mark.asyncio
async def test_open_file_rejects_unknown_path() -> None:
    service, _, _, _ = _service()
    draft = SkillDraft(name="file-skill", description="Has one file.", instructions="Body text.")
    revision = await service.create_authored(VALIDATION_SCOPE, draft)

    opened = await service.open_file(VALIDATION_SCOPE, revision.revision_id, "SKILL.md")
    assert b"Body text." in opened.data

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.open_file(VALIDATION_SCOPE, revision.revision_id, "unknown.txt")
    assert excinfo.value.code == "skill_path_rejected"


@pytest.mark.asyncio
async def test_purge_expired_imports_deletes_staging() -> None:
    service, catalog, objects, box = _service()
    await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )
    assert objects.staged

    box[0] = box[0] + timedelta(hours=25)
    purged = await service.purge_expired_imports(VALIDATION_SCOPE, now=box[0])

    assert purged == 1
    assert not objects.staged
    assert catalog._imports == {}


@pytest.mark.asyncio
async def test_set_enabled_disables_latest_revision() -> None:
    service, catalog, _, _ = _service()
    draft = SkillDraft(name="toggle-skill", description="Toggle me.", instructions="Body.")
    revision = await service.create_authored(VALIDATION_SCOPE, draft)

    await service.set_enabled(VALIDATION_SCOPE, revision.asset_id, False)

    latest = (await catalog.list_revisions("skill", revision.asset_id))[-1]
    assert latest.status == AssetRevisionStatus.DISABLED


@pytest.mark.asyncio
async def test_commit_import_rejects_empty_or_duplicate_selections() -> None:
    service, _, _, _ = _service()
    preview = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(VALIDATION_SCOPE, preview.import_id, ())
    assert excinfo.value.code == "skill_not_found_in_package"

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(
            VALIDATION_SCOPE,
            preview.import_id,
            (ImportSelection(root="demo-skill"), ImportSelection(root="demo-skill")),
        )
    assert excinfo.value.code == "skill_not_found_in_package"


@pytest.mark.asyncio
async def test_commit_import_validates_every_selection_before_writing_any() -> None:
    """I-1: a package with two skills where only one selection is invalid
    must not leave the valid one partially committed."""
    service, catalog, _, _ = _service()
    other_skill_md = (
        "---\nname: other-skill\ndescription: Another demo skill.\n---\n\nOther body.\n"
    )
    preview = await service.stage_import(
        VALIDATION_SCOPE,
        upload=FilesUpload(
            files={
                "demo-skill/SKILL.md": _SKILL_MD.encode(),
                "other-skill/SKILL.md": other_skill_md.encode(),
            }
        ),
    )

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.commit_import(
            VALIDATION_SCOPE,
            preview.import_id,
            (
                ImportSelection(root="demo-skill"),
                ImportSelection(root="no-such-root"),
            ),
        )
    assert excinfo.value.code == "skill_not_found_in_package"
    # Nothing was written for the valid "demo-skill" selection either.
    assert catalog._skills == {}


@pytest.mark.asyncio
async def test_commit_import_converges_with_a_concurrent_writer_via_deterministic_ids() -> None:
    """I-2: a row already inserted under the deterministic (import_id, root)
    id - as a concurrent committer would leave it - is reused rather than
    rejected or duplicated, and (fix round 2) the winner's promoted file
    remains intact and readable: with deterministic object identity a real
    content-addressed store's promote() is idempotent, so the loser's
    promotion of the same content resolves to the exact same ref as the
    winner's — cleaning it up on "conflict" would have deleted live data."""
    service, catalog, objects, _ = _service()
    preview = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )
    asset_id = _deterministic_id("skill", preview.import_id, "demo-skill")
    revision_id = _deterministic_id("skill-rev", preview.import_id, "demo-skill")
    skill_md_bytes = _SKILL_MD.encode()

    # Simulate a concurrent committer: it independently staged and promoted
    # the identical file content under the same deterministic identity, and
    # already inserted its revision, before this call runs.
    winner_staged = await _stage_bytes_for_test(objects, skill_md_bytes)
    winner_ref = await objects.promote(
        winner_staged,
        winner_staged.sha256,
        identity=_file_identity(asset_id, revision_id, "SKILL.md"),
        attributes={"revision": revision_id, "kind": "skill", "relativePath": "SKILL.md"},
    )
    concurrent_revision = SkillRevision(
        revision_id=revision_id,
        asset_id=asset_id,
        display_name="demo-skill",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("x"),
        instruction_template_digest=text_digest("y"),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template="y",
        description="Demo skill used in authoring tests.",
        source_kind="imported",
        files=(
            SkillFile(
                relative_path="SKILL.md",
                size_bytes=len(skill_md_bytes),
                sha256=hashlib.sha256(skill_md_bytes).hexdigest(),
                category="skill",
                attached=False,
            ),
        ),
    )
    await catalog.insert_skill_revision(
        concurrent_revision,
        base_revision_id=None,
        file_object_refs={"SKILL.md": str(winner_ref)},
    )

    (revision,) = await service.commit_import(
        VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
    )

    assert revision.revision_id == revision_id
    assert revision.asset_id == asset_id
    assert len(await catalog.list_revisions("skill", asset_id)) == 1
    # The winner's object is still intact: open_file still returns the
    # original content via the persisted (shared) ref.
    opened = await service.open_file(VALIDATION_SCOPE, revision_id, "SKILL.md")
    assert opened.data == skill_md_bytes
    assert ObjectRef(str(winner_ref)) in objects.objects


@pytest.mark.asyncio
async def test_commit_import_preserves_winner_file_on_generic_error_after_winner_exists() -> None:
    """Fix round 3: a non-RevisionConflict failure on the loser's own
    insert attempt (lock-wait timeout, dropped connection, ...) must not
    delete a winner's already-persisted, shared, content-addressed file
    just because the error wasn't a RevisionConflict."""
    service, catalog, objects, _ = _service()
    preview = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )
    asset_id = _deterministic_id("skill", preview.import_id, "demo-skill")
    revision_id = _deterministic_id("skill-rev", preview.import_id, "demo-skill")
    skill_md_bytes = _SKILL_MD.encode()

    # The winner already independently staged, promoted and committed this
    # exact deterministic revision.
    winner_staged = await _stage_bytes_for_test(objects, skill_md_bytes)
    winner_ref = await objects.promote(
        winner_staged,
        winner_staged.sha256,
        identity=_file_identity(asset_id, revision_id, "SKILL.md"),
        attributes={"revision": revision_id, "kind": "skill", "relativePath": "SKILL.md"},
    )
    concurrent_revision = SkillRevision(
        revision_id=revision_id,
        asset_id=asset_id,
        display_name="demo-skill",
        scope=VALIDATION_SCOPE,
        content_digest=text_digest("x"),
        instruction_template_digest=text_digest("y"),
        applicable_tasks=frozenset({"knowledge.answer"}),
        instruction_template="y",
        description="Demo skill used in authoring tests.",
        source_kind="imported",
        files=(
            SkillFile(
                relative_path="SKILL.md",
                size_bytes=len(skill_md_bytes),
                sha256=hashlib.sha256(skill_md_bytes).hexdigest(),
                category="skill",
                attached=False,
            ),
        ),
    )
    await catalog.insert_skill_revision(
        concurrent_revision,
        base_revision_id=None,
        file_object_refs={"SKILL.md": str(winner_ref)},
    )

    # This caller's own insert attempt fails for an unrelated reason (not a
    # RevisionConflict) after the winner has already landed its row.
    catalog._fail_inserts = 1

    with pytest.raises(RuntimeError):
        await service.commit_import(
            VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
        )

    # The winner's file must still be intact and readable.
    opened = await service.open_file(VALIDATION_SCOPE, revision_id, "SKILL.md")
    assert opened.data == skill_md_bytes
    assert ObjectRef(str(winner_ref)) in objects.objects


@pytest.mark.asyncio
async def test_commit_import_cleans_up_promoted_objects_on_insert_failure() -> None:
    """M-5: when the catalog write fails for a reason other than a
    recoverable deterministic-id collision, newly promoted objects are
    deleted rather than leaked."""
    catalog = _FakeCatalog(fail_inserts=1)
    service, _, objects, _ = _service(catalog=catalog)
    preview = await service.stage_import(
        VALIDATION_SCOPE, upload=FilesUpload(files={"demo-skill/SKILL.md": _SKILL_MD.encode()})
    )

    with pytest.raises(RuntimeError):
        await service.commit_import(
            VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
        )

    assert not objects.objects


@pytest.mark.asyncio
async def test_revise_requires_existing_skill() -> None:
    service, _, _, _ = _service()
    draft = SkillDraft(name="ghost-skill", description="Does not exist yet.", instructions="X.")

    with pytest.raises(AssetRevisionRejected):
        await service.revise(VALIDATION_SCOPE, "skill-does-not-exist", "no-base", draft)


@pytest.mark.asyncio
async def test_revise_rejects_renaming_to_a_name_owned_by_a_different_skill() -> None:
    """I-9: `revise` never checked `find_skill_by_name` for a rename, so an
    authored Skill could be renamed onto another Skill's name."""
    service, _, _, _ = _service()
    alpha = await service.create_authored(
        VALIDATION_SCOPE,
        SkillDraft(name="alpha-skill", description="First skill.", instructions="Alpha body."),
    )
    beta = await service.create_authored(
        VALIDATION_SCOPE,
        SkillDraft(name="beta-skill", description="Second skill.", instructions="Beta body."),
    )

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.revise(
            VALIDATION_SCOPE,
            beta.asset_id,
            beta.revision_id,
            SkillDraft(
                name="alpha-skill",
                description="Renamed onto alpha's name.",
                instructions="Beta body, renamed.",
            ),
        )
    assert excinfo.value.code == "skill_name_conflict"

    # Revising without changing the name (collides only with itself) still
    # succeeds -- the check only rejects a *different* asset's name.
    unchanged = await service.revise(
        VALIDATION_SCOPE,
        alpha.asset_id,
        alpha.revision_id,
        SkillDraft(
            name="alpha-skill",
            description="Same name, new description.",
            instructions="Alpha body v2.",
        ),
    )
    assert unchanged.asset_id == alpha.asset_id
    assert unchanged.revision_id != alpha.revision_id


@pytest.mark.asyncio
async def test_empty_file_is_recorded_without_touching_the_object_store() -> None:
    """I-4: the real object store rejects empty uploads, so a zero-byte file
    must never be staged or promoted; open_file still returns empty bytes."""
    service, catalog, objects, _ = _service()
    preview = await service.stage_import(
        VALIDATION_SCOPE,
        upload=FilesUpload(
            files={
                "demo-skill/SKILL.md": _SKILL_MD.encode(),
                "demo-skill/empty.txt": b"",
            }
        ),
    )
    # Only SKILL.md was staged; the empty file never touched put_staged.
    assert len(objects.staged) == 1

    (revision,) = await service.commit_import(
        VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
    )

    empty_file = next(f for f in revision.files if f.relative_path == "empty.txt")
    assert empty_file.size_bytes == 0
    # SKILL.md was promoted (and no object was ever created for the empty
    # file); promote() does not consume staging, matching the real store.
    assert objects.objects
    opened = await service.open_file(VALIDATION_SCOPE, revision.revision_id, "empty.txt")
    assert opened.data == b""
    assert opened.size == 0


@pytest.mark.asyncio
async def test_relative_path_with_unsafe_identity_characters_still_promotes() -> None:
    """I-4: a relative path containing characters the real object store's
    identity segments forbid (space, unicode, "+", "@") must still import
    cleanly because identity is derived from a hash of the path."""
    service, catalog, objects, _ = _service()
    tricky_path = "demo-skill/notes é+extra@home.txt"
    preview = await service.stage_import(
        VALIDATION_SCOPE,
        upload=FilesUpload(
            files={
                "demo-skill/SKILL.md": _SKILL_MD.encode(),
                tricky_path: b"tricky content",
            }
        ),
    )

    (revision,) = await service.commit_import(
        VALIDATION_SCOPE, preview.import_id, (ImportSelection(root="demo-skill"),)
    )

    opened = await service.open_file(
        VALIDATION_SCOPE, revision.revision_id, "notes é+extra@home.txt"
    )
    assert opened.data == b"tricky content"


@pytest.mark.asyncio
async def test_open_file_maps_missing_object_to_clean_rejection() -> None:
    """M-9: a file whose persisted reference no longer resolves (object
    deleted, or a malformed legacy locator) surfaces as skill_path_rejected,
    not a raw storage exception."""
    service, catalog, objects, _ = _service()
    draft = SkillDraft(
        name="vanishing-skill", description="Will lose its object.", instructions="Body."
    )
    revision = await service.create_authored(VALIDATION_SCOPE, draft)
    ref = await catalog.get_skill_file_object_ref(revision.revision_id, "SKILL.md")
    await objects.delete(ObjectRef(ref))

    with pytest.raises(SkillPackageRejected) as excinfo:
        await service.open_file(VALIDATION_SCOPE, revision.revision_id, "SKILL.md")
    assert excinfo.value.code == "skill_path_rejected"
