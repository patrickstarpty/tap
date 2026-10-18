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
    AssetRevisionRejected,
    AssetRevisionStatus,
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
    ) -> tuple[SkillRevision, ...]: ...

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
        try:
            await self._objects.delete(StagingRef(locator))
        except ObjectMissingError:
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
        preview_by_root = {entry["root"]: entry for entry in preview_skills}
        revisions: list[SkillRevision] = []
        for selection in selections:
            entry = preview_by_root.get(selection.root)
            if entry is None:
                raise SkillPackageRejected(
                    "skill_not_found_in_package",
                    f"no staged skill at root {selection.root!r}",
                )
            if entry["conflict_skill_id"] is not None and selection.as_new_revision_of is None:
                raise SkillPackageRejected(
                    "skill_name_conflict",
                    f"a skill named {entry['name']!r} already exists; "
                    "select as_new_revision_of to attach a new revision",
                )
            asset_id = selection.as_new_revision_of
            base_revision_id: str | None = None
            if asset_id is not None:
                existing_revisions = await self._catalog.list_revisions("skill", asset_id)
                if not existing_revisions:
                    raise SkillPackageRejected(
                        "skill_not_found_in_package", f"unknown skill asset {asset_id!r}"
                    )
                base_revision_id = existing_revisions[-1].revision_id
            else:
                asset_id = f"skill-{uuid4().hex[:16]}"

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
                revision_id=f"skill-rev-{uuid4().hex[:16]}",
                asset_id=asset_id,
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
            )
            file_refs = await self._promote_staged_files(
                asset_id, revision.revision_id, file_entries
            )
            revision = await self._catalog.insert_skill_revision(
                revision, base_revision_id=base_revision_id, file_object_refs=file_refs
            )
            revisions.append(revision)

        result_json = self._encode_result(revisions)
        won = await self._catalog.set_skill_import_committed(import_id, result_json)
        if not won:
            refreshed = await self._catalog.get_skill_import(import_id)
            if refreshed is not None and refreshed.committed_result_json is not None:
                return self._decode_result(refreshed.committed_result_json)
        return tuple(revisions)

    async def _promote_staged_files(
        self, asset_id: str, revision_id: str, file_entries: Sequence[Mapping[str, object]]
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        for file in file_entries:
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
                identity=f"skills/{asset_id}/{revision_id}/{file['relative_path']}",
                attributes={"revision": revision_id, "kind": str(file["category"])},
            )
            refs[str(file["relative_path"])] = str(ref)
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
        asset_id = f"skill-{uuid4().hex[:16]}"
        revision, scan = self._build_authored_revision(scope, draft, asset_id=asset_id)
        file_refs = await self._promote_authored_files(asset_id, revision.revision_id, scan)
        return await self._catalog.insert_skill_revision(
            revision, base_revision_id=None, file_object_refs=file_refs
        )

    async def revise(
        self,
        scope: ProjectScopeContext,
        skill_id: str,
        base_revision_id: str | None,
        draft: SkillDraft,
    ) -> SkillRevision:
        self._assert_scope(scope)
        revision, scan = self._build_authored_revision(
            scope, draft, asset_id=skill_id, adopted_from_revision_id=base_revision_id
        )
        file_refs = await self._promote_authored_files(skill_id, revision.revision_id, scan)
        return await self._catalog.insert_skill_revision(
            revision, base_revision_id=base_revision_id, file_object_refs=file_refs
        )

    def _build_authored_revision(
        self,
        scope: ProjectScopeContext,
        draft: SkillDraft,
        *,
        asset_id: str,
        adopted_from_revision_id: str | None = None,
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
        )
        return revision, scan

    async def _promote_authored_files(
        self, asset_id: str, revision_id: str, scan: PackageScan
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        for file in scan.skills[0].files:
            staged = await self._stage_bytes(scan.contents[file.relative_path])
            ref = await self._objects.promote(
                staged,
                staged.sha256,
                identity=f"skills/{asset_id}/{revision_id}/{file.relative_path}",
                attributes={"revision": revision_id, "kind": file.category},
            )
            refs[file.relative_path] = str(ref)
        return refs

    async def set_enabled(self, scope: ProjectScopeContext, skill_id: str, enabled: bool) -> None:
        self._assert_scope(scope)
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
        return await self._objects.open_verified(ObjectRef(ref))
