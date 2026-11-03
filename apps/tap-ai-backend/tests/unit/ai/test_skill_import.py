from __future__ import annotations

import pytest

from tap.modules.ai.application.skill_import import (
    SkillImportRejected,
    parse_skill_markdown,
    render_skill_markdown,
)

VALID = b"""---
name: release-notes
description: Summarize release notes with citations.
allowed-tools: [knowledge.search, Bash]
metadata:
  owner: qa
license: internal
---

# Release notes

Cite every claim.
"""


def _rejected(raw: bytes) -> str:
    with pytest.raises(SkillImportRejected) as error:
        parse_skill_markdown(raw)
    return error.value.code


def test_valid_skill_is_parsed_with_digest_and_untrusted_tool_requests() -> None:
    import hashlib

    parsed = parse_skill_markdown(VALID)

    assert parsed.name == "release-notes"
    assert parsed.description == "Summarize release notes with citations."
    assert parsed.instructions == "# Release notes\n\nCite every claim."
    assert parsed.requested_tools == ("knowledge.search", "Bash")
    assert parsed.warnings == ("Bash",)
    assert parsed.front_matter["metadata"] == {"owner": "qa"}
    assert parsed.front_matter["license"] == "internal"
    assert parsed.content_digest == "sha256:" + hashlib.sha256(VALID).hexdigest()
    assert parsed.source_markdown.encode("utf-8") == VALID


@pytest.mark.parametrize(
    "literal",
    [
        b"0x" + b"f" * 5000,
        b"0" + b"7" * 5000,
        b"0b" + b"1" * 5000,
        b"0x10000000000000000",
        b"-0x8000000000000001",
        b"9223372036854775808",
        b"-9223372036854775809",
        b"9" * 5000,
        b".inf",
        b"-.inf",
        b".nan",
        b"1.0e+999",
        b"-1.0e+999",
        b"1" + b":59" * 2000,
    ],
)
def test_out_of_range_numbers_are_rejected(literal: bytes) -> None:
    raw = b"---\nname: a\ndescription: b\nx: " + literal + b"\n---\nBody\n"
    assert _rejected(raw) == "skill-front-matter-invalid"


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (b"0x7fffffffffffffff", 2**63 - 1),
        (b"-9223372036854775807", -(2**63) + 1),
        (b"017", 15),
        (b"0o17", "0o17"),
        (b"0b101", 5),
        (b"1.5", 1.5),
    ],
)
def test_front_matter_is_json_serializable(literal: bytes, expected: object) -> None:
    import json

    raw = (
        b"---\nname: a\ndescription: b\nwhen: 2026-01-02\nx: "
        + literal
        + b"\nmetadata: {n: [1, 2.5, true, null]}\n---\nBody\n"
    )
    parsed = parse_skill_markdown(raw)
    assert parsed.front_matter["x"] == expected
    json.dumps(dict(parsed.front_matter))


def test_missing_front_matter_is_rejected() -> None:
    assert _rejected(b"# Release notes\n\nCite every claim.\n") == "skill-front-matter-missing"
    assert _rejected(b"---\nname: a\ndescription: b\n# no closing\n") == (
        "skill-front-matter-missing"
    )


@pytest.mark.parametrize(
    "front_matter",
    [
        b"name: !!python/object/apply:os.system ['echo pwned']\ndescription: x",
        b"!!python/object:builtins.dict {}",
        b"- a\n- b",
        b"name: [unclosed",
        b"1: one\nname: a\ndescription: b",
        b"anchor: &a [x]\nname: a\ndescription: b\nref: *a",
        b"name: a\ndescription: b\nallowed-tools: {a: b}",
        b"name: a\ndescription: b\nmetadata: [x]",
        b"name: a\ndescription: b\nblob: !!binary aGVsbG8=",
        b"name: a\ndescription: b\nname: c",
        b"name: a\ndescription: b\nmetadata: {owner: x, owner: y}",
        b"name: a\ndescription: b\n<<: {extra: x}",
        b"name: a\ndescription: b\nmetadata:\n  <<: {owner: x}",
        b"name: a\ndescription: b\nx: " + b"[" * 5000 + b"]" * 5000,
        b"name: a\ndescription: b\nx: " + b"{a: " * 5000 + b"1" + b"}" * 5000,
        b"name: a\ndescription: b\nx: " + b"[" * 40 + b"]" * 40,
        b"name: a\ndescription: b\nx: " + b"9" * 5000,
    ],
)
def test_unsafe_or_malformed_front_matter_is_rejected(front_matter: bytes) -> None:
    assert _rejected(b"---\n" + front_matter + b"\n---\nBody\n") == "skill-front-matter-invalid"


@pytest.mark.parametrize("name", ["My Skill", "my_skill", "-skill", "a" * 65, "", 42])
def test_invalid_names_are_rejected(name: object) -> None:
    import json

    raw = f"---\nname: {json.dumps(name)}\ndescription: ok\n---\nBody\n".encode()
    assert _rejected(raw) == "skill-name-invalid"


def test_invalid_description_and_empty_body_are_rejected() -> None:
    assert _rejected(b"---\nname: a\n---\nBody\n") == "skill-description-invalid"
    long = ("x" * 1025).encode()
    assert _rejected(b"---\nname: a\ndescription: " + long + b"\n---\nBody\n") == (
        "skill-description-invalid"
    )
    assert _rejected(b"---\nname: a\ndescription: ok\n---\n  \n\n") == "skill-body-empty"


def test_size_and_encoding_limits() -> None:
    padding = b"x" * (65 * 1024)
    assert _rejected(b"---\nname: a\ndescription: ok\n---\n" + padding) == "skill-too-large"
    assert _rejected(b"---\nname: a\ndescription: ok\n---\n\xff\xfe") == "skill-not-utf8"
    exact = b"---\nname: a\ndescription: ok\n---\n"
    assert parse_skill_markdown(exact + b"x" * (64 * 1024 - len(exact))).name == "a"


def test_body_code_and_markup_are_stored_verbatim() -> None:
    body = "Run nothing.\n\n```bash\nrm -rf /\n```\n\n<script>alert(1)</script>"
    raw = f"---\nname: inert\ndescription: ok\n---\n{body}\n".encode()

    parsed = parse_skill_markdown(raw)

    assert parsed.instructions == body
    assert "rm -rf /" in parsed.source_markdown
    assert "<script>alert(1)</script>" in parsed.source_markdown


def test_crlf_and_comma_separated_allowed_tools_are_accepted() -> None:
    raw = (
        b"---\r\nname: crlf\r\ndescription: ok\r\nallowed-tools: knowledge.search, Read\r\n"
        b"---\r\nBody\r\n"
    )
    parsed = parse_skill_markdown(raw)
    assert parsed.requested_tools == ("knowledge.search", "Read")
    assert parsed.warnings == ("Read",)
    assert parsed.instructions == "Body"


def test_rendered_skill_round_trips() -> None:
    instructions = "Line one: with colon\n\n---\n\n- bullet"
    raw = render_skill_markdown("round-trip", "Description: with: colons", instructions)

    parsed = parse_skill_markdown(raw)

    assert (parsed.name, parsed.description, parsed.instructions) == (
        "round-trip",
        "Description: with: colons",
        instructions,
    )
    assert parsed.requested_tools == ()
    assert render_skill_markdown("round-trip", "Description: with: colons", instructions) == raw
    with pytest.raises(SkillImportRejected):
        render_skill_markdown("Bad Name", "ok", "body")


def test_default_known_tools_follow_builtin_registry() -> None:
    from tap.modules.ai.application.skill_import import BUILTIN_TOOL_IDS
    from tap.modules.ai.application.tool_registry import builtin_tools

    assert frozenset(builtin_tools()) == BUILTIN_TOOL_IDS
    assert parse_skill_markdown(VALID).warnings == ("Bash",)
