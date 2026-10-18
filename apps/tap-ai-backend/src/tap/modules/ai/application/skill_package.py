"""Parse and validate skill packages without ever executing their contents.

These are pure functions over bytes: no filesystem writes, no imports of
package code, no network access. ``scan_zip`` streams and byte-accounts the
decompressed size as it reads (zip-bomb defense) and rejects path traversal,
symlinks and device-file entries before any content is trusted. ``scan_files``
is the shared core used by ``scan_zip`` (after stripping a single top-level
directory and extracting bytes) and by ``scan_markdown`` (a single in-memory
``SKILL.md``), and can also be called directly with an already-assembled
``{relative_path: bytes}`` mapping.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass

import yaml

from tap.modules.ai.domain.assets import SkillFile, SkillFileCategory

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
MAX_SKILL_MD_BYTES = 256 * 1024
MAX_ATTACHED_BYTES = 64 * 1024

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)
_REFERENCE_PATTERN = re.compile(r"\[[^\]]*\]\((references/[^)\s]+)\)|`(references/[^`]+)`")
_CHUNK_BYTES = 1 << 20


class SkillPackageRejected(Exception):
    """A skill package failed validation and must not be imported or executed."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    description: str
    frontmatter: Mapping[str, object]
    body: str


@dataclass(frozen=True, slots=True)
class ScannedSkill:
    root: str
    manifest: SkillManifest
    files: tuple[SkillFile, ...]
    attached_reference_text: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PackageScan:
    skills: tuple[ScannedSkill, ...]
    contents: Mapping[str, bytes]


def parse_skill_markdown(raw: bytes) -> SkillManifest:
    if not isinstance(raw, bytes):
        raise SkillPackageRejected("skill_frontmatter_invalid", "SKILL.md must be raw bytes")
    if len(raw) > MAX_SKILL_MD_BYTES:
        raise SkillPackageRejected(
            "skill_package_too_large", "SKILL.md exceeds the per-file size limit"
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SkillPackageRejected(
            "skill_frontmatter_invalid", "SKILL.md must be valid UTF-8"
        ) from error
    match = _FRONTMATTER_PATTERN.match(text)
    if match is None:
        raise SkillPackageRejected(
            "skill_frontmatter_invalid", "SKILL.md must start with a --- frontmatter block"
        )
    frontmatter_text, body = match.group(1), match.group(2)
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as error:
        raise SkillPackageRejected(
            "skill_frontmatter_invalid", "SKILL.md frontmatter must be valid YAML"
        ) from error
    if not isinstance(frontmatter, Mapping):
        raise SkillPackageRejected(
            "skill_frontmatter_invalid", "SKILL.md frontmatter must be a YAML mapping"
        )
    name = frontmatter.get("name")
    if not isinstance(name, str) or len(name) > 64 or _NAME_PATTERN.fullmatch(name) is None:
        raise SkillPackageRejected(
            "skill_frontmatter_invalid",
            "frontmatter name is required, must match ^[a-z0-9]+(-[a-z0-9]+)*$ "
            "and be at most 64 characters",
        )
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillPackageRejected(
            "skill_frontmatter_invalid",
            "frontmatter description is required and must be at most 1024 characters",
        )
    return SkillManifest(name=name, description=description, frontmatter=frontmatter, body=body)


def _validate_relative_path(path: str) -> None:
    if not isinstance(path, str) or not path:
        raise SkillPackageRejected("skill_path_rejected", f"invalid path: {path!r}")
    segments = path.split("/")
    if any(segment in ("", "..") for segment in segments):
        raise SkillPackageRejected("skill_path_rejected", f"invalid path: {path!r}")


def _strip_common_prefix(paths: list[str]) -> list[str]:
    if not paths:
        return paths
    top_segments: set[str] = set()
    for path in paths:
        if "/" not in path:
            return paths
        top_segments.add(path.split("/", 1)[0])
    if len(top_segments) != 1:
        return paths
    prefix = next(iter(top_segments)) + "/"
    return [path[len(prefix) :] for path in paths]


def _categorize(relative_path: str) -> SkillFileCategory:
    if relative_path == "SKILL.md":
        return "skill"
    first = relative_path.split("/", 1)[0]
    if first == "scripts":
        return "script"
    if first == "references":
        return "reference"
    if first == "assets":
        return "asset"
    return "other"


def _extract_references(body: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _REFERENCE_PATTERN.finditer(body):
        path = match.group(1) or match.group(2)
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _owner_root(path: str, roots: list[str]) -> str | None:
    for root in roots:
        if root == "":
            continue
        if path == f"{root}/SKILL.md" or path.startswith(f"{root}/"):
            return root
    if "" in roots:
        return ""
    return None


def scan_files(files: Mapping[str, bytes]) -> PackageScan:
    if not isinstance(files, Mapping):
        raise SkillPackageRejected(
            "skill_path_rejected", "package contents must be a mapping of path to bytes"
        )
    contents: dict[str, bytes] = {}
    for path, content in files.items():
        _validate_relative_path(path)
        if not isinstance(content, bytes):
            raise SkillPackageRejected("skill_path_rejected", f"invalid content for path: {path!r}")
        contents[path] = content
    if len(contents) > MAX_FILES:
        raise SkillPackageRejected(
            "skill_package_too_large",
            f"package contains {len(contents)} files, exceeding the limit of {MAX_FILES}; "
            "check that the archive contains only skill folders (each with a SKILL.md) "
            "rather than an entire repository",
        )

    roots = sorted(
        {posixpath.dirname(path) for path in contents if posixpath.basename(path) == "SKILL.md"},
        key=len,
        reverse=True,
    )
    if not roots:
        raise SkillPackageRejected(
            "skill_not_found_in_package", "no SKILL.md was found in the package"
        )

    members_by_root: dict[str, list[str]] = {root: [] for root in roots}
    for path in contents:
        owner = _owner_root(path, roots)
        if owner is not None:
            members_by_root[owner].append(path)

    skills: list[ScannedSkill] = []
    for root in roots:
        prefix = f"{root}/" if root else ""
        skill_md_path = f"{root}/SKILL.md" if root else "SKILL.md"
        manifest = parse_skill_markdown(contents[skill_md_path])

        referenced = _extract_references(manifest.body)
        attached_parts: list[str] = []
        attached_relative_paths: set[str] = set()
        warnings: list[str] = []
        budget_used = 0
        for reference in referenced:
            if not reference.endswith((".md", ".txt")):
                continue
            full_path = prefix + reference if prefix else reference
            reference_content = contents.get(full_path)
            if reference_content is None:
                continue
            try:
                text = reference_content.decode("utf-8")
            except UnicodeDecodeError:
                warnings.append(f"reference '{reference}' is not valid UTF-8 and was not attached")
                continue
            if budget_used + len(reference_content) > MAX_ATTACHED_BYTES:
                warnings.append(
                    f"reference '{reference}' exceeds the attached reference budget "
                    "and was not attached"
                )
                continue
            budget_used += len(reference_content)
            attached_parts.append(text)
            attached_relative_paths.add(reference)

        member_paths = sorted(members_by_root[root])
        skill_files = tuple(
            SkillFile(
                relative_path=(member[len(prefix) :] if prefix else member),
                size_bytes=len(contents[member]),
                sha256=hashlib.sha256(contents[member]).hexdigest(),
                category=_categorize(member[len(prefix) :] if prefix else member),
                attached=(member[len(prefix) :] if prefix else member) in attached_relative_paths,
            )
            for member in member_paths
        )
        skills.append(
            ScannedSkill(
                root=root,
                manifest=manifest,
                files=skill_files,
                attached_reference_text="\n\n".join(attached_parts),
                warnings=tuple(warnings),
            )
        )

    return PackageScan(skills=tuple(skills), contents=dict(contents))


def scan_markdown(text: str) -> PackageScan:
    if not isinstance(text, str):
        raise SkillPackageRejected("skill_frontmatter_invalid", "SKILL.md text must be a string")
    return scan_files({"SKILL.md": text.encode("utf-8")})


def scan_zip(data: bytes) -> PackageScan:
    if not isinstance(data, bytes):
        raise SkillPackageRejected("skill_package_too_large", "package must be raw bytes")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise SkillPackageRejected(
            "skill_package_too_large", "the compressed package exceeds the archive size limit"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise SkillPackageRejected(
            "skill_path_rejected", "the package is not a valid zip archive"
        ) from error

    with archive:
        file_infos = [info for info in archive.infolist() if not info.is_dir()]
        names: list[str] = []
        for info in file_infos:
            name = info.filename
            _validate_relative_path(name)
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR):
                raise SkillPackageRejected(
                    "skill_path_rejected", f"unsupported file type in package entry: {name}"
                )
            names.append(name)

        if len(names) > MAX_FILES:
            raise SkillPackageRejected(
                "skill_package_too_large",
                f"package contains {len(names)} files, exceeding the limit of {MAX_FILES}; "
                "check that the archive contains only skill folders (each with a SKILL.md) "
                "rather than an entire repository",
            )

        stripped_names = _strip_common_prefix(names)

        contents: dict[str, bytes] = {}
        total_extracted = 0
        for info, stripped_name in zip(file_infos, stripped_names):
            chunks: list[bytes] = []
            with archive.open(info) as handle:
                while True:
                    chunk = handle.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    total_extracted += len(chunk)
                    if total_extracted > MAX_EXTRACTED_BYTES:
                        raise SkillPackageRejected(
                            "skill_package_too_large",
                            "the decompressed package exceeds the extraction size limit",
                        )
                    chunks.append(chunk)
            contents[stripped_name] = b"".join(chunks)

    return scan_files(contents)
