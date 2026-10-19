"""Skill package parsing and validation never executes code and enforces limits."""

from __future__ import annotations

import io
import warnings
import zipfile

import pytest

from tap.modules.ai.application.skill_package import (
    MAX_ATTACHED_BYTES,
    SkillPackageRejected,
    parse_skill_markdown,
    scan_files,
    scan_markdown,
    scan_zip,
)


def _skill_md(
    name: str = "my-skill", description: str = "Does the thing.", body: str = ""
) -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}".encode("utf-8")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_minimal_skill_md() -> None:
    manifest = parse_skill_markdown(_skill_md(body="Hello body."))
    assert manifest.name == "my-skill"
    assert manifest.description == "Does the thing."
    assert manifest.body == "Hello body."
    assert manifest.frontmatter["name"] == "my-skill"


def test_unknown_frontmatter_kept() -> None:
    raw = (
        b"---\n"
        b"name: my-skill\n"
        b"description: Does the thing.\n"
        b"license: MIT\n"
        b"compatibility: [python]\n"
        b"metadata:\n"
        b"  owner: team-a\n"
        b"allowed-tools: [knowledge.search]\n"
        b"custom-field: kept\n"
        b"---\n"
        b"body\n"
    )
    manifest = parse_skill_markdown(raw)
    assert manifest.frontmatter["license"] == "MIT"
    assert manifest.frontmatter["compatibility"] == ["python"]
    assert manifest.frontmatter["metadata"] == {"owner": "team-a"}
    assert manifest.frontmatter["allowed-tools"] == ["knowledge.search"]
    assert manifest.frontmatter["custom-field"] == "kept"


def test_invalid_name_rejected() -> None:
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(_skill_md(name="My Skill"))
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_missing_description_rejected() -> None:
    raw = b"---\nname: my-skill\n---\nbody\n"
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_bom_and_crlf_are_accepted() -> None:
    raw = "﻿---\r\nname: my-skill\r\ndescription: Does the thing.\r\n---\r\nbody\r\n".encode("utf-8")
    manifest = parse_skill_markdown(raw)
    assert manifest.name == "my-skill"
    assert manifest.description == "Does the thing."


def test_single_top_level_dir_is_stripped() -> None:
    data = _zip_bytes(
        {
            "my-skill/SKILL.md": _skill_md(),
            "my-skill/references/notes.md": b"notes",
        }
    )
    scan = scan_zip(data)
    assert len(scan.skills) == 1
    assert scan.skills[0].root == ""
    paths = {file.relative_path for file in scan.skills[0].files}
    assert paths == {"SKILL.md", "references/notes.md"}


def test_multiple_skills_detected() -> None:
    scan = scan_files(
        {
            "skills/a/SKILL.md": _skill_md(name="skill-a"),
            "skills/b/SKILL.md": _skill_md(name="skill-b"),
        }
    )
    roots = {skill.root for skill in scan.skills}
    assert roots == {"skills/a", "skills/b"}
    names = {skill.manifest.name for skill in scan.skills}
    assert names == {"skill-a", "skill-b"}


def test_no_skill_md_rejected() -> None:
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_files({"README.md": b"hello"})
    assert excinfo.value.code == "skill_not_found_in_package"


def test_path_traversal_rejected() -> None:
    data = _zip_bytes({"../x": b"escape", "SKILL.md": _skill_md()})
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(data)
    assert excinfo.value.code == "skill_path_rejected"


def test_symlink_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("SKILL.md")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "target")
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(buffer.getvalue())
    assert excinfo.value.code == "skill_path_rejected"


def test_zip_bomb_rejected() -> None:
    data = _zip_bytes({"SKILL.md": b"\x00" * (60 * 1024 * 1024)})
    assert len(data) < 1024 * 1024
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(data)
    assert excinfo.value.code == "skill_package_too_large"


def test_repo_sized_zip_reports_file_limit_with_hint() -> None:
    entries = {f"file-{index}.txt": b"x" for index in range(501)}
    data = _zip_bytes(entries)
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(data)
    assert excinfo.value.code == "skill_package_too_large"
    assert "SKILL.md" in excinfo.value.detail


def test_references_attached_in_order_within_budget() -> None:
    body = (
        "See `references/b.md` for context, then [a](references/a.md), "
        "and [too-big](references/too-big.md)."
    )
    scan = scan_files(
        {
            "SKILL.md": _skill_md(body=body),
            "references/a.md": b"content a",
            "references/b.md": b"content b",
            "references/too-big.md": b"x" * (MAX_ATTACHED_BYTES + 1),
        }
    )
    skill = scan.skills[0]
    assert skill.attached_reference_text == "content b\n\ncontent a"
    attached = {file.relative_path: file.attached for file in skill.files}
    assert attached["references/b.md"] is True
    assert attached["references/a.md"] is True
    assert attached["references/too-big.md"] is False
    assert any("too-big.md" in warning for warning in skill.warnings)


def test_unreferenced_reference_not_attached() -> None:
    scan = scan_files(
        {
            "SKILL.md": _skill_md(body="no references mentioned here."),
            "references/unused.md": b"unused content",
        }
    )
    skill = scan.skills[0]
    assert skill.attached_reference_text == ""
    unused = next(file for file in skill.files if file.relative_path == "references/unused.md")
    assert unused.attached is False
    assert unused.category == "reference"


def test_non_utf8_reference_is_skipped_with_warning() -> None:
    scan = scan_files(
        {
            "SKILL.md": _skill_md(body="see [bad](references/bad.md)."),
            "references/bad.md": b"\xff\xfe\x00\x01",
        }
    )
    skill = scan.skills[0]
    assert skill.attached_reference_text == ""
    bad = next(file for file in skill.files if file.relative_path == "references/bad.md")
    assert bad.attached is False
    assert any("bad.md" in warning for warning in skill.warnings)


def test_scripts_never_attached() -> None:
    scan = scan_files(
        {
            "SKILL.md": _skill_md(body="see [script](scripts/run.py) and references/run.py."),
            "scripts/run.py": b"print('hi')",
        }
    )
    skill = scan.skills[0]
    script_file = next(file for file in skill.files if file.relative_path == "scripts/run.py")
    assert script_file.category == "script"
    assert script_file.attached is False
    assert skill.attached_reference_text == ""


def test_scan_markdown_wraps_single_skill() -> None:
    text = _skill_md().decode("utf-8")
    scan = scan_markdown(text)
    assert len(scan.skills) == 1
    assert scan.skills[0].manifest.name == "my-skill"


# --- Fix round 1: security review findings -------------------------------------


def test_windows_style_and_control_char_paths_rejected() -> None:
    bad_paths = (
        "..\\..\\evil.txt",
        "C:\\evil.txt",
        "C:/evil.txt",
        "./x/./y.txt",
        "a\x00b",
        "a\nb",
    )
    for bad_path in bad_paths:
        with pytest.raises(SkillPackageRejected) as excinfo:
            scan_files({"SKILL.md": _skill_md(), bad_path: b"data"})
        assert excinfo.value.code == "skill_path_rejected", bad_path


def test_duplicate_zip_entry_names_rejected() -> None:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", _skill_md(name="skill-one"))
            archive.writestr("SKILL.md", _skill_md(name="skill-two"))
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(buffer.getvalue())
    assert excinfo.value.code == "skill_path_rejected"


def test_case_insensitive_duplicate_paths_rejected() -> None:
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_files(
            {
                "SKILL.md": _skill_md(),
                "References/A.md": b"upper",
                "references/a.md": b"lower",
            }
        )
    assert excinfo.value.code == "skill_path_rejected"


def test_encrypted_zip_entry_rejected() -> None:
    # zipfile.writestr resets flag_bits on write, so the "encrypted" general
    # purpose bit flag (bit 0) is set directly on the written bytes: in both the
    # local file header (flag field at offset 6) and the central directory
    # record (flag field at offset 8).
    data = bytearray(_zip_bytes({"SKILL.md": _skill_md()}))
    local_offset = data.find(b"PK\x03\x04")
    central_offset = data.find(b"PK\x01\x02")
    assert local_offset != -1 and central_offset != -1
    local_flags = int.from_bytes(data[local_offset + 6 : local_offset + 8], "little")
    central_flags = int.from_bytes(data[central_offset + 8 : central_offset + 10], "little")
    data[local_offset + 6 : local_offset + 8] = (local_flags | 0x1).to_bytes(2, "little")
    data[central_offset + 8 : central_offset + 10] = (central_flags | 0x1).to_bytes(2, "little")
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(bytes(data))
    assert excinfo.value.code == "skill_path_rejected"


def test_corrupt_zip_entry_rejected() -> None:
    marker = b"CORRUPT-ME-PAYLOAD"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("SKILL.md", _skill_md(body=marker.decode()))
    data = bytearray(buffer.getvalue())
    offset = data.find(marker)
    assert offset != -1
    data[offset] ^= 0xFF
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(bytes(data))
    assert excinfo.value.code == "skill_path_rejected"


def test_yaml_alias_rejected() -> None:
    raw = b"---\nname: my-skill\ndescription: &d Does the thing.\nother: *d\n---\nbody\n"
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_yaml_non_string_key_rejected() -> None:
    raw = (
        b"---\n"
        b"name: my-skill\n"
        b"description: Does the thing.\n"
        b"metadata:\n"
        b"  1: numeric-key\n"
        b"---\n"
        b"body\n"
    )
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_yaml_date_value_rejected() -> None:
    raw = b"---\nname: my-skill\ndescription: Does the thing.\nupdated: 2024-01-01\n---\nbody\n"
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_skill_order_is_deterministic() -> None:
    scan = scan_files(
        {
            "skills/b/SKILL.md": _skill_md(name="skill-b"),
            "skills/a/SKILL.md": _skill_md(name="skill-a"),
        }
    )
    assert [skill.root for skill in scan.skills] == ["skills/a", "skills/b"]


def test_duplicate_skill_name_rejected() -> None:
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_files(
            {
                "skills/a/SKILL.md": _skill_md(name="same-name"),
                "skills/b/SKILL.md": _skill_md(name="same-name"),
            }
        )
    assert excinfo.value.code == "skill_frontmatter_invalid"


def test_frontmatter_closing_delimiter_must_be_on_own_line() -> None:
    raw = b"---\nname: my-skill\ndescription: Does the thing.\n---not-a-fence\nbody\n"
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"


# --- Fix round 2: security review follow-up -------------------------------------


def test_unsupported_compression_method_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_BZIP2) as archive:
        archive.writestr("SKILL.md", _skill_md())
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(buffer.getvalue())
    assert excinfo.value.code == "skill_path_rejected"


def test_invalid_utf8_flagged_entry_name_rejected() -> None:
    # Set the UTF-8 general-purpose flag (bit 0x800) on both the local file
    # header and central directory record, then corrupt the first filename
    # byte to an invalid UTF-8 start byte, reproducing a zip that makes
    # zipfile.ZipFile(...) itself raise UnicodeDecodeError while reading the
    # central directory.
    name_bytes = b"SKILL.md"
    data = bytearray(_zip_bytes({"SKILL.md": _skill_md()}))
    local_offset = data.find(b"PK\x03\x04")
    central_offset = data.find(b"PK\x01\x02")
    assert local_offset != -1 and central_offset != -1
    local_flags = int.from_bytes(data[local_offset + 6 : local_offset + 8], "little")
    central_flags = int.from_bytes(data[central_offset + 8 : central_offset + 10], "little")
    data[local_offset + 6 : local_offset + 8] = (local_flags | 0x800).to_bytes(2, "little")
    data[central_offset + 8 : central_offset + 10] = (central_flags | 0x800).to_bytes(2, "little")
    local_name_offset = local_offset + 30
    central_name_offset = central_offset + 46
    assert data[local_name_offset : local_name_offset + len(name_bytes)] == name_bytes
    assert data[central_name_offset : central_name_offset + len(name_bytes)] == name_bytes
    data[local_name_offset] = 0xFF
    data[central_name_offset] = 0xFF
    with pytest.raises(SkillPackageRejected) as excinfo:
        scan_zip(bytes(data))
    assert excinfo.value.code == "skill_path_rejected"


def test_reference_paths_are_nfc_normalized_before_lookup() -> None:
    nfc_path = "references/café.md"  # "é" as one composed code point
    nfd_reference_in_body = "references/café.md"  # "e" + combining acute accent
    scan = scan_files(
        {
            "SKILL.md": _skill_md(body=f"see [notes]({nfd_reference_in_body})."),
            nfc_path: b"content",
        }
    )
    skill = scan.skills[0]
    assert skill.attached_reference_text == "content"
    attached = {file.relative_path: file.attached for file in skill.files}
    assert attached[nfc_path] is True


def test_non_finite_float_rejected() -> None:
    for literal in (".nan", ".inf", "-.inf"):
        raw = (
            f"---\nname: my-skill\ndescription: Does the thing.\nscore: {literal}\n---\nbody\n"
        ).encode("utf-8")
        with pytest.raises(SkillPackageRejected) as excinfo:
            parse_skill_markdown(raw)
        assert excinfo.value.code == "skill_frontmatter_invalid", literal


def test_yaml_recursion_error_rejected() -> None:
    depth = 3000
    nested = "[" * depth + "]" * depth
    raw = (
        f"---\nname: my-skill\ndescription: Does the thing.\nnested: {nested}\n---\nbody\n"
    ).encode("utf-8")
    with pytest.raises(SkillPackageRejected) as excinfo:
        parse_skill_markdown(raw)
    assert excinfo.value.code == "skill_frontmatter_invalid"
