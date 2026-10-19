"""Author, import and revise Skill assets without ever executing package content.

Imported or hand-authored Skill content is staged, scanned (Task 3's
``scan_zip``/``scan_files``/``scan_markdown``) and promoted into the approved
catalog (Task 2's ``MysqlAssetCatalog``) through a narrow catalog port
(``SkillAuthoringCatalog``) and the provider-neutral ``ObjectStorePort``.
Package bytes are staged once (at ``stage_import``) and only promoted to
immutable objects on ``commit_import``; nothing here ever imports or executes
packaged code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol, cast
from uuid import uuid4

import yaml

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.application.skill_package import (
    PackageScan,
    SkillPackageRejected,
    scan_files,
    scan_markdown,
    scan_zip,
)
from tap.modules.ai.domain.assets import (
    AiAgentRevision,
    AssetRevisionRejected,
    AssetRevisionStatus,
    RevisionConflict,
    SkillFile,
    SkillRevision,
    text_digest,
)
from tap.platform.storage.objects import (
    ObjectMissingError,
    ObjectRef,
    ObjectStorePort,
    PutObjectRequest,
    StagedObject,
    StagingRef,
    VerifiedObject,
)

_STAGING_TTL = timedelta(hours=24)
_IMPORTED_APPLICABLE_TASKS = frozenset({"knowledge.answer"})
# Zero-byte files are never staged/promoted (the real object store rejects
# empty uploads); this marker is stored in place of an object reference and
# ``open_file`` recognizes it to synthesize an empty read without touching
# the object store.
_EMPTY_FILE_MARKER = "skill-empty-file"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _deterministic_id(prefix: str, import_id: str, root: str) -> str:
    """Derive a stable asset/revision id from (import_id, root).

    Two callers committing the same staged import concurrently (or a caller
    retrying before either has recorded a result) compute the identical id,
    so their writes converge on the same database row instead of creating
    duplicate visible Skill assets.
    """
    digest = hashlib.sha256(f"{import_id}\0{root}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _file_identity(asset_id: str, revision_id: str, relative_path: str) -> str:
    """A path-hash identity: real object stores restrict identity segments to
    ``[A-Za-z0-9_-][A-Za-z0-9._-]{0,255}``, which arbitrary relative paths
    (spaces, unicode, leading dots, ``+``, ``@``) can violate. The relative
    path itself is preserved in the object's attributes instead."""
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()
    return f"skills/{asset_id}/{revision_id}/{digest}"


@dataclass(frozen=True, slots=True)
class ZipUpload:
    data: bytes


@dataclass(frozen=True, slots=True)
class FilesUpload:
    files: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class TextUpload:
    text: str


SkillUpload = ZipUpload | FilesUpload | TextUpload


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportSelection:
    root: str
    as_new_revision_of: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillDraft:
    name: str
    description: str
    instructions: str
    applicable_tasks: frozenset[str] = _IMPORTED_APPLICABLE_TASKS


@dataclass(frozen=True, slots=True, kw_only=True)
class PreviewSkill:
    root: str
    name: str
    description: str
    frontmatter: Mapping[str, object]
    files: tuple[SkillFile, ...]
    warnings: tuple[str, ...]
    conflict_skill_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportPreview:
    import_id: str
    expires_at: datetime
    skills: tuple[PreviewSkill, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillImportRecord:
    import_id: str
    preview_json: Mapping[str, object]
    staging_locators: tuple[str, ...]
    expires_at: datetime
    committed_result_json: object | None


class SkillAuthoringCatalog(Protocol):
    """The narrow slice of the approved AI catalog this service depends on."""

    async def insert_skill_revision(
        self,
        revision: SkillRevision,
        *,
        base_revision_id: str | None,
        file_object_refs: Mapping[str, str] | None = None,
    ) -> SkillRevision: ...

    async def set_asset_enabled(
        self, kind: Literal["agent", "skill"], asset_id: str, enabled: bool
    ) -> None: ...

    async def list_revisions(
        self, kind: Literal["agent", "skill"], asset_id: str
    ) -> tuple[AiAgentRevision, ...] | tuple[SkillRevision, ...]: ...

    async def find_skill_by_name(
        self, scope: ProjectScopeContext, name: str
    ) -> SkillRevision | None: ...

    async def get_skill_file_object_ref(
        self, revision_id: str, relative_path: str
    ) -> str | None: ...

    async def create_skill_import(
        self,
        *,
        import_id: str,
        preview_json: Mapping[str, object],
        staging_locators: Sequence[str],
        expires_at: datetime,
    ) -> None: ...

    async def get_skill_import(self, import_id: str) -> SkillImportRecord | None: ...

    async def set_skill_import_committed(self, import_id: str, result_json: object) -> bool: ...

    async def purge_expired_skill_imports(
        self, *, now: datetime, limit: int
    ) -> tuple[SkillImportRecord, ...]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class _CommitPlan:
    """A validated, about-to-be-written Skill revision for one selection."""

    entry: Mapping[str, object]
    asset_id: str
    base_revision_id: str | None
    revision_id: str
    status: AssetRevisionStatus = AssetRevisionStatus.ENABLED


Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillAuthoringService:
    def __init__(
        self,
        catalog: SkillAuthoringCatalog,
        objects: ObjectStorePort,
        *,
        scope: ProjectScopeContext,
        clock: Clock = _utcnow,
    ) -> None:
        self._catalog = catalog
        self._objects = objects
        self._scope = scope
        self._clock = clock

    def _assert_scope(self, scope: ProjectScopeContext) -> None:
        if type(scope) is not ProjectScopeContext or scope != self._scope:
            raise AssetRevisionRejected()

    # -- import: stage ----------------------------------------------------

    async def purge_expired_imports(
        self, scope: ProjectScopeContext, *, now: datetime, limit: int = 20
    ) -> int:
        self._assert_scope(scope)
        records = await self._catalog.purge_expired_skill_imports(now=now, limit=limit)
        for record in records:
            for locator in record.staging_locators:
                await self._discard_staging(locator)
        return len(records)

    async def _discard_staging(self, locator: str) -> None:
        # Best-effort: a purge sweep must not abort stage_import over a
        # provider hiccup or an object that is already gone.
        try:
            await self._objects.delete(StagingRef(locator))
        except Exception:
            pass

    async def stage_import(
        self, scope: ProjectScopeContext, *, upload: SkillUpload
    ) -> ImportPreview:
        self._assert_scope(scope)
        now = self._clock()
        await self.purge_expired_imports(scope, now=now)

        scan = self._scan(upload)
        preview_entries: list[dict[str, object]] = []
        public_skills: list[PreviewSkill] = []
        staging_locators: list[str] = []

        for skill in scan.skills:
            existing = await self._catalog.find_skill_by_name(scope, skill.manifest.name)
            conflict_skill_id = existing.asset_id if existing is not None else None

            staged_files: list[dict[str, object]] = []
            for file in skill.files:
                if file.size_bytes == 0:
                    # The real object store rejects empty uploads; record the
                    # file without staging/promoting any bytes.
                    staged_files.append(
                        {
                            "relative_path": file.relative_path,
                            "size_bytes": 0,
                            "sha256": file.sha256,
                            "category": file.category,
                            "attached": file.attached,
                            "staging_ref": None,
                        }
                    )
                    continue
                full_path = (
                    f"{skill.root}/{file.relative_path}" if skill.root else file.relative_path
                )
                staged = await self._stage_bytes(scan.contents[full_path])
                staging_locators.append(str(staged.ref))
                staged_files.append(
                    {
                        "relative_path": file.relative_path,
                        "size_bytes": file.size_bytes,
                        "sha256": file.sha256,
                        "category": file.category,
                        "attached": file.attached,
                        "staging_ref": str(staged.ref),
                    }
                )

            preview_entries.append(
                {
                    "root": skill.root,
                    "name": skill.manifest.name,
                    "description": skill.manifest.description,
                    "frontmatter": dict(skill.manifest.frontmatter),
                    "body": skill.manifest.body,
                    "attached_reference_text": skill.attached_reference_text,
                    "warnings": list(skill.warnings),
                    "conflict_skill_id": conflict_skill_id,
                    "files": staged_files,
                }
            )
            public_skills.append(
                PreviewSkill(
                    root=skill.root,
                    name=skill.manifest.name,
                    description=skill.manifest.description,
                    frontmatter=skill.manifest.frontmatter,
                    files=skill.files,
                    warnings=skill.warnings,
                    conflict_skill_id=conflict_skill_id,
                )
            )

        import_id = f"skill-import-{uuid4().hex[:16]}"
        expires_at = now + _STAGING_TTL
        await self._catalog.create_skill_import(
            import_id=import_id,
            preview_json={"skills": preview_entries},
            staging_locators=staging_locators,
            expires_at=expires_at,
        )
        return ImportPreview(
            import_id=import_id, expires_at=expires_at, skills=tuple(public_skills)
        )

    def _scan(self, upload: SkillUpload) -> PackageScan:
        if isinstance(upload, ZipUpload):
            return scan_zip(upload.data)
        if isinstance(upload, FilesUpload):
            return scan_files(upload.files)
        if isinstance(upload, TextUpload):
            return scan_markdown(upload.text)
        raise SkillPackageRejected("skill_path_rejected", "unsupported upload type")

    async def _stage_bytes(self, data: bytes) -> StagedObject:
        async def content():  # type: ignore[no-untyped-def]
            yield data

        return await self._objects.put_staged(
            PutObjectRequest(content(), max(len(data), 1), "application/octet-stream")
        )

    # -- import: commit -----------------------------------------------------

    async def commit_import(
        self,
        scope: ProjectScopeContext,
        import_id: str,
        selections: Sequence[ImportSelection],
    ) -> tuple[SkillRevision, ...]:
        self._assert_scope(scope)
        now = self._clock()
        record = await self._catalog.get_skill_import(import_id)
        if record is None or record.expires_at <= now:
            raise SkillPackageRejected(
                "import_expired", "the staged import does not exist or has expired"
            )
        if record.committed_result_json is not None:
            return self._decode_result(record.committed_result_json)

        preview_skills = cast(list[dict[str, object]], record.preview_json["skills"])
        preview_by_root = {cast(str, entry["root"]): entry for entry in preview_skills}
        plans = await self._validate_selections(scope, import_id, preview_by_root, selections)

        revisions: list[SkillRevision] = []
        for plan in plans:
            revisions.append(await self._commit_one(scope, plan))

        result_json = self._encode_result(revisions)
        won = await self._catalog.set_skill_import_committed(import_id, result_json)
        if not won:
            refreshed = await self._catalog.get_skill_import(import_id)
            if refreshed is not None and refreshed.committed_result_json is not None:
                return self._decode_result(refreshed.committed_result_json)
        return tuple(revisions)

    async def _validate_selections(
        self,
        scope: ProjectScopeContext,
        import_id: str,
        preview_by_root: Mapping[str, dict[str, object]],
        selections: Sequence[ImportSelection],
    ) -> list[_CommitPlan]:
        """Validate every selection before any write (I-1): an unknown root,
        duplicate root, empty selection list, unresolved name conflict or
        missing ``as_new_revision_of`` target must fail the whole commit
        before any Skill is persisted, not partway through."""
        if not selections:
            raise SkillPackageRejected(
                "skill_not_found_in_package", "no skills were selected to commit"
            )
        seen_roots: set[str] = set()
        plans: list[_CommitPlan] = []
        for selection in selections:
            if selection.root in seen_roots:
                raise SkillPackageRejected(
                    "skill_not_found_in_package",
                    f"duplicate selection for root {selection.root!r}",
                )
            seen_roots.add(selection.root)
            entry = preview_by_root.get(selection.root)
            if entry is None:
                raise SkillPackageRejected(
                    "skill_not_found_in_package",
                    f"no staged skill at root {selection.root!r}",
                )
            # The id this exact (import_id, root) would create or converge
            # on (I-2) when not attaching to an explicit existing asset.
            deterministic_asset_id = (
                _deterministic_id("skill", import_id, selection.root)
                if selection.as_new_revision_of is None
                else None
            )
            # Re-check the name conflict against the catalog's current
            # state (another skill of the same name may have been created
            # since this import was staged) — but a match that is this
            # exact commit's own deterministic asset (a concurrent or
            # retried committer for the same import_id/root) is not a
            # conflict, it is the convergence I-2 relies on.
            fresh_conflict = await self._catalog.find_skill_by_name(scope, cast(str, entry["name"]))
            conflict_skill_id = (
                fresh_conflict.asset_id
                if fresh_conflict is not None
                else entry["conflict_skill_id"]
            )
            if (
                conflict_skill_id is not None
                and conflict_skill_id != deterministic_asset_id
                and selection.as_new_revision_of is None
            ):
                raise SkillPackageRejected(
                    "skill_name_conflict",
                    f"a skill named {entry['name']!r} already exists; "
                    "select as_new_revision_of to attach a new revision",
                )
            asset_id = selection.as_new_revision_of
            base_revision_id: str | None = None
            status = AssetRevisionStatus.ENABLED
            if asset_id is not None:
                existing_revisions = await self._catalog.list_revisions("skill", asset_id)
                if not existing_revisions:
                    raise SkillPackageRejected(
                        "skill_not_found_in_package", f"unknown skill asset {asset_id!r}"
                    )
                base = cast(SkillRevision, existing_revisions[-1])
                if base.source_kind == "built_in":
                    raise SkillPackageRejected(
                        "skill_built_in_immutable",
                        "built-in skills cannot receive an imported revision",
                    )
                base_revision_id = base.revision_id
                # Decide revise-of-disabled explicitly (C-2): keep the
                # asset disabled across an import commit rather than
                # silently re-enabling it.
                status = base.status
            else:
                asset_id = deterministic_asset_id
            assert asset_id is not None
            revision_id = _deterministic_id("skill-rev", import_id, selection.root)
            plans.append(
                _CommitPlan(
                    entry=entry,
                    asset_id=asset_id,
                    base_revision_id=base_revision_id,
                    revision_id=revision_id,
                    status=status,
                )
            )
        return plans

    async def _commit_one(self, scope: ProjectScopeContext, plan: _CommitPlan) -> SkillRevision:
        entry = plan.entry
        instruction_template = cast(str, entry["body"])
        attached_reference_text = cast(str, entry["attached_reference_text"])
        combined = f"{instruction_template}{attached_reference_text}"
        file_entries = cast(list[dict[str, object]], entry["files"])
        files = tuple(
            SkillFile(
                relative_path=cast(str, file["relative_path"]),
                size_bytes=cast(int, file["size_bytes"]),
                sha256=cast(str, file["sha256"]),
                category=cast(
                    "Literal['skill', 'reference', 'script', 'asset', 'other']",
                    file["category"],
                ),
                attached=cast(bool, file["attached"]),
            )
            for file in file_entries
        )
        revision = SkillRevision(
            revision_id=plan.revision_id,
            asset_id=plan.asset_id,
            display_name=cast(str, entry["name"]),
            scope=scope,
            content_digest=text_digest(json.dumps(entry["frontmatter"], sort_keys=True)),
            instruction_template_digest=text_digest(combined),
            applicable_tasks=_IMPORTED_APPLICABLE_TASKS,
            instruction_template=instruction_template,
            description=cast(str, entry["description"]),
            source_kind="imported",
            attached_references=attached_reference_text,
            frontmatter=cast("Mapping[str, object]", entry["frontmatter"]),
            package_digest=text_digest(combined),
            files=files,
            status=plan.status,
        )
        file_refs = await self._promote_staged_files(plan.asset_id, plan.revision_id, file_entries)
        try:
            return await self._catalog.insert_skill_revision(
                revision, base_revision_id=plan.base_revision_id, file_object_refs=file_refs
            )
        except RevisionConflict:
            # Deterministic ids (I-2) mean a concurrent or retried committer
            # for the same (import_id, root) collides on this exact row: the
            # survivor's insert already succeeded, so fetch and reuse it
            # rather than surfacing a conflict for a commit that, from the
            # caller's perspective, already happened.
            existing = await self._find_revision(plan.asset_id, plan.revision_id)
            if existing is not None:
                # Do NOT clean up file_refs here: with deterministic
                # per-(asset, revision, relative_path) object identity, a
                # real content-addressed store's promote() is idempotent —
                # our just-promoted refs and the survivor's persisted refs
                # resolve to the exact same objects. Deleting them would
                # delete the survivor's live data, not an orphan.
                return existing
            await self._cleanup_promoted(file_refs)
            raise
        except BaseException:
            # A non-RevisionConflict failure here (lock-wait timeout,
            # dropped connection, ...) does not by itself mean nobody else
            # committed this exact deterministic (asset, revision): a
            # winner may have already landed the row before this error
            # surfaced. Check before cleaning up, and treat an inconclusive
            # check the same as "a winner exists" — an orphaned object is
            # recoverable; deleting a winner's shared, content-addressed
            # file is not.
            if not await self._skip_cleanup_for_existing_revision(plan.asset_id, plan.revision_id):
                await self._cleanup_promoted(file_refs)
            raise

    async def _skip_cleanup_for_existing_revision(self, asset_id: str, revision_id: str) -> bool:
        try:
            existing = await self._find_revision(asset_id, revision_id)
        except Exception:
            # Prefer leaking orphaned objects over risking deletion of a
            # winner's live, shared files when we cannot tell which case
            # this is.
            return True
        return existing is not None

    async def _find_revision(self, asset_id: str, revision_id: str) -> SkillRevision | None:
        for revision in await self._catalog.list_revisions("skill", asset_id):
            if revision.revision_id == revision_id:
                return cast(SkillRevision, revision)
        return None

    async def _cleanup_promoted(self, file_refs: Mapping[str, str]) -> None:
        # Best-effort: the DB write failing (or losing a commit race) must
        # not itself raise over a provider cleanup error.
        for ref in file_refs.values():
            if ref == _EMPTY_FILE_MARKER:
                continue
            try:
                await self._objects.delete(ObjectRef(ref))
            except Exception:
                pass

    async def _promote_staged_files(
        self, asset_id: str, revision_id: str, file_entries: Sequence[Mapping[str, object]]
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        for file in file_entries:
            relative_path = str(file["relative_path"])
            if file.get("staging_ref") is None:
                refs[relative_path] = _EMPTY_FILE_MARKER
                continue
            digest = f"sha256:{file['sha256']}"
            staged = StagedObject(
                StagingRef(str(file["staging_ref"])),
                digest,
                cast(int, file["size_bytes"]),
                "application/octet-stream",
            )
            ref = await self._objects.promote(
                staged,
                digest,
                identity=_file_identity(asset_id, revision_id, relative_path),
                attributes={
                    "revision": revision_id,
                    "kind": str(file["category"]),
                    "relativePath": relative_path,
                },
            )
            refs[relative_path] = str(ref)
        return refs

    def _encode_result(self, revisions: Sequence[SkillRevision]) -> list[dict[str, object]]:
        return [
            {
                "revision_id": revision.revision_id,
                "asset_id": revision.asset_id,
                "display_name": revision.display_name,
                "description": revision.description,
                "source_kind": revision.source_kind,
                "content_digest": revision.content_digest,
                "instruction_template_digest": revision.instruction_template_digest,
                "instruction_template": revision.instruction_template,
                "attached_references": revision.attached_references,
                "frontmatter": (
                    dict(revision.frontmatter) if revision.frontmatter is not None else None
                ),
                "package_digest": revision.package_digest,
                "applicable_tasks": sorted(revision.applicable_tasks),
                "status": revision.status.value,
                "adopted_from_revision_id": revision.adopted_from_revision_id,
                "files": [
                    {
                        "relative_path": file.relative_path,
                        "size_bytes": file.size_bytes,
                        "sha256": file.sha256,
                        "category": file.category,
                        "attached": file.attached,
                    }
                    for file in revision.files
                ],
            }
            for revision in revisions
        ]

    def _decode_result(self, data: object) -> tuple[SkillRevision, ...]:
        if not isinstance(data, list):
            raise SkillPackageRejected("import_expired", "the committed import result is malformed")
        return tuple(
            SkillRevision(
                revision_id=item["revision_id"],
                asset_id=item["asset_id"],
                display_name=item["display_name"],
                scope=self._scope,
                content_digest=item["content_digest"],
                instruction_template_digest=item["instruction_template_digest"],
                applicable_tasks=frozenset(item["applicable_tasks"]),
                status=AssetRevisionStatus(item["status"]),
                adopted_from_revision_id=item["adopted_from_revision_id"],
                instruction_template=item["instruction_template"],
                description=item["description"],
                source_kind=item["source_kind"],
                attached_references=item["attached_references"],
                frontmatter=item["frontmatter"],
                package_digest=item["package_digest"],
                files=tuple(SkillFile(**file) for file in item["files"]),
            )
            for item in data
        )

    # -- authoring ------------------------------------------------------

    async def create_authored(self, scope: ProjectScopeContext, draft: SkillDraft) -> SkillRevision:
        self._assert_scope(scope)
        existing = await self._catalog.find_skill_by_name(scope, draft.name)
        if existing is not None:
            raise SkillPackageRejected(
                "skill_name_conflict", f"a skill named {draft.name!r} already exists"
            )
        asset_id = f"skill-{uuid4().hex[:16]}"
        revision, scan = self._build_authored_revision(scope, draft, asset_id=asset_id)
        file_refs = await self._promote_authored_files(asset_id, revision.revision_id, scan)
        try:
            return await self._catalog.insert_skill_revision(
                revision, base_revision_id=None, file_object_refs=file_refs
            )
        except BaseException:
            await self._cleanup_promoted(file_refs)
            raise

    async def revise(
        self,
        scope: ProjectScopeContext,
        skill_id: str,
        base_revision_id: str,
        draft: SkillDraft,
    ) -> SkillRevision:
        self._assert_scope(scope)
        history = await self._catalog.list_revisions("skill", skill_id)
        if not history:
            raise AssetRevisionRejected()
        latest = cast(SkillRevision, history[-1])
        if latest.source_kind == "built_in":
            raise SkillPackageRejected(
                "skill_built_in_immutable",
                "built-in skills cannot be revised; import or create a new skill instead",
            )
        existing = await self._catalog.find_skill_by_name(scope, draft.name)
        if existing is not None and existing.asset_id != skill_id:
            raise SkillPackageRejected(
                "skill_name_conflict", f"a skill named {draft.name!r} already exists"
            )
        revision, scan = self._build_authored_revision(
            scope,
            draft,
            asset_id=skill_id,
            adopted_from_revision_id=base_revision_id,
            # Decide revise-of-disabled explicitly (C-2): keep the asset
            # disabled across a revise rather than silently re-enabling it.
            status=latest.status,
        )
        file_refs = await self._promote_authored_files(skill_id, revision.revision_id, scan)
        try:
            return await self._catalog.insert_skill_revision(
                revision, base_revision_id=base_revision_id, file_object_refs=file_refs
            )
        except BaseException:
            await self._cleanup_promoted(file_refs)
            raise

    def _build_authored_revision(
        self,
        scope: ProjectScopeContext,
        draft: SkillDraft,
        *,
        asset_id: str,
        adopted_from_revision_id: str | None = None,
        status: AssetRevisionStatus = AssetRevisionStatus.ENABLED,
    ) -> tuple[SkillRevision, PackageScan]:
        if not isinstance(draft, SkillDraft):
            raise SkillPackageRejected("skill_frontmatter_invalid", "draft must be a SkillDraft")
        frontmatter_text = yaml.safe_dump(
            {"name": draft.name, "description": draft.description}, sort_keys=False
        )
        skill_md_text = f"---\n{frontmatter_text}---\n\n{draft.instructions}\n"
        scan = scan_markdown(skill_md_text)
        scanned = scan.skills[0]
        manifest = scanned.manifest
        applicable_tasks = frozenset(draft.applicable_tasks) or _IMPORTED_APPLICABLE_TASKS
        revision = SkillRevision(
            revision_id=f"skill-rev-{uuid4().hex[:16]}",
            asset_id=asset_id,
            display_name=manifest.name,
            scope=scope,
            content_digest=text_digest(skill_md_text),
            instruction_template_digest=text_digest(
                f"{manifest.body}{scanned.attached_reference_text}"
            ),
            applicable_tasks=applicable_tasks,
            instruction_template=manifest.body,
            description=manifest.description,
            source_kind="authored",
            attached_references=scanned.attached_reference_text,
            frontmatter=manifest.frontmatter,
            package_digest=text_digest(skill_md_text),
            adopted_from_revision_id=adopted_from_revision_id,
            files=scanned.files,
            status=status,
        )
        return revision, scan

    async def _promote_authored_files(
        self, asset_id: str, revision_id: str, scan: PackageScan
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        for file in scan.skills[0].files:
            if file.size_bytes == 0:
                refs[file.relative_path] = _EMPTY_FILE_MARKER
                continue
            staged = await self._stage_bytes(scan.contents[file.relative_path])
            ref = await self._objects.promote(
                staged,
                staged.sha256,
                identity=_file_identity(asset_id, revision_id, file.relative_path),
                attributes={
                    "revision": revision_id,
                    "kind": file.category,
                    "relativePath": file.relative_path,
                },
            )
            refs[file.relative_path] = str(ref)
        return refs

    async def set_enabled(self, scope: ProjectScopeContext, skill_id: str, enabled: bool) -> None:
        self._assert_scope(scope)
        history = await self._catalog.list_revisions("skill", skill_id)
        if not history:
            raise AssetRevisionRejected()
        if cast(SkillRevision, history[-1]).source_kind == "built_in":
            raise SkillPackageRejected(
                "skill_built_in_immutable", "built-in skills cannot be disabled or enabled"
            )
        await self._catalog.set_asset_enabled("skill", skill_id, enabled)

    async def open_file(
        self, scope: ProjectScopeContext, revision_id: str, relative_path: str
    ) -> VerifiedObject:
        self._assert_scope(scope)
        ref = await self._catalog.get_skill_file_object_ref(revision_id, relative_path)
        if ref is None:
            raise SkillPackageRejected(
                "skill_path_rejected", "path is not part of this revision's file table"
            )
        if ref == _EMPTY_FILE_MARKER:
            return VerifiedObject(
                b"",
                f"sha256:{_EMPTY_SHA256}",
                0,
                "application/octet-stream",
                _EMPTY_FILE_MARKER,
                (),
            )
        try:
            return await self._objects.open_verified(ObjectRef(ref))
        except (ObjectMissingError, ValueError, TypeError):
            # A malformed or no-longer-present object reference (including
            # the legacy identity-string fallback) surfaces as a clean
            # not-found rather than a raw storage exception.
            raise SkillPackageRejected(
                "skill_path_rejected", "the file's object is no longer available"
            ) from None
