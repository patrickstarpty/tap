"""Page lifecycle is canonical data, never inferred from ambient browser tabs."""

import json
from copy import deepcopy

import pytest

from tap_platform.lca.definition import canonical_bytes, validate_definition
from tap_platform.lca.generator import generate_playwright


def step(operation, arguments=None, **fields):
    return dict(
        id=operation,
        operation=operation,
        arguments=arguments or {},
        timeout_ms=1000,
        **fields,
    )


def flow(*steps):
    return dict(
        schema_version="lca-debug-v1",
        allowed_origins=["https://fixture.example"],
        targets=[dict(id="button", kind="test_id", value="button")],
        parameters=[],
        steps=list(steps),
    )


def test_explicit_pages_round_trip_without_changing_legacy_bytes():
    legacy = flow(step("click", target_id="button"))
    assert (
        canonical_bytes(validate_definition(legacy))
        == json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    )
    raw = flow(
        step(
            "open_page", {"page_id": "second", "url": "https://fixture.example/second"}
        ),
        step("click", target_id="button", page_id="second"),
        step("switch_page", {"page_id": "second"}),
        step("close_page", {"page_id": "main"}),
    )
    parsed = validate_definition(raw)
    assert json.loads(canonical_bytes(parsed)) == raw
    assert generate_playwright(parsed) == generate_playwright(
        validate_definition(deepcopy(raw))
    )


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("click_popup", {"page_id": "popup"}),
        ("click_dialog", {"action": "accept", "type": "alert", "message": "hello"}),
        ("click_dialog", {"action": "dismiss", "type": "confirm", "message": "hello"}),
        (
            "click_dialog",
            {
                "action": "accept",
                "type": "prompt",
                "message": "hello",
                "prompt_text": "Ada",
            },
        ),
    ],
)
def test_explicit_event_actions_round_trip(operation, arguments):
    raw = flow(step(operation, arguments, target_id="button"))
    assert json.loads(canonical_bytes(validate_definition(raw))) == raw


@pytest.mark.parametrize(
    "steps",
    [
        [step("click", target_id="button", page_id="missing")],
        [step("switch_page", {"page_id": "missing"})],
        [step("close_page", {"page_id": "main"})],
        [step("open_page", {"page_id": "main", "url": "https://fixture.example/"})],
        [step("click_popup", {"page_id": "main"}, target_id="button")],
        [step("open_page", {"page_id": "other", "url": "https://evil.example/"})],
        [step("open_page", {"page_id": "other", "url": "file:///tmp/secret"})],
        [step("switch_page", {"page_id": "main"}, page_id="main")],
        [
            step(
                "open_page",
                {"page_id": "other", "url": "https://fixture.example/"},
                target_id="button",
            )
        ],
    ],
)
def test_missing_unsafe_or_ambiguous_page_references_rejected(steps):
    with pytest.raises(ValueError):
        validate_definition(flow(*steps))


@pytest.mark.parametrize(
    "after",
    [
        step("click", target_id="button", page_id="other"),
        step("switch_page", {"page_id": "other"}),
        step("open_page", {"page_id": "other", "url": "https://fixture.example/"}),
        step("click_popup", {"page_id": "other"}, target_id="button"),
    ],
)
def test_closed_page_ids_cannot_be_referenced_or_reused(after):
    opening = step("open_page", {"page_id": "other", "url": "https://fixture.example/"})
    opening["id"] = "opening"
    with pytest.raises(ValueError):
        validate_definition(
            flow(opening, step("close_page", {"page_id": "other"}), after)
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "accept", "type": "confirm"},
        {"action": "accept", "type": "beforeunload", "message": ""},
        {"action": "automatic", "type": "confirm", "message": ""},
        {"action": "dismiss", "type": "prompt", "message": "", "prompt_text": "Ada"},
        {"action": "accept", "type": "confirm", "message": "", "prompt_text": "Ada"},
        {"action": {"parameter": "action"}, "type": "confirm", "message": ""},
    ],
)
def test_dialog_acceptance_requires_exact_explicit_match(arguments):
    with pytest.raises(ValueError):
        validate_definition(flow(step("click_dialog", arguments, target_id="button")))


@pytest.mark.parametrize("page_id", ["", "Invalid", "../path", {"parameter": "page"}])
def test_page_ids_are_static_canonical_identifiers(page_id):
    raw = flow(
        step("open_page", {"page_id": page_id, "url": "https://fixture.example/"})
    )
    raw["parameters"] = [dict(name="page", type="string", required=True)]
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize(
    "asset_id",
    ["12345678123412341234123456789abc", "12345678-1234-1234-1234-123456789abc"],
)
def test_frozen_upload_asset_and_download_actions_round_trip(asset_id):
    raw = flow(
        step(
            "upload_file",
            {"asset_id": asset_id, "sha256": "a" * 64},
            target_id="button",
        ),
        step("download_file", target_id="button"),
    )
    parsed = validate_definition(raw)
    assert json.loads(canonical_bytes(parsed)) == raw
    assert generate_playwright(parsed) == generate_playwright(parsed)


@pytest.mark.parametrize(
    "arguments",
    [
        {"asset_id": "../../etc/passwd", "sha256": "a" * 64},
        {"asset_id": "https://fixture.example/file", "sha256": "a" * 64},
        {"asset_id": "12345678123412341234123456789abc", "sha256": "a" * 63},
        {"asset_id": "12345678123412341234123456789abc", "sha256": "A" * 64},
        {
            "asset_id": "12345678123412341234123456789abc",
            "sha256": {"parameter": "digest"},
        },
        {"asset_id": {"parameter": "asset"}, "sha256": "a" * 64},
        {"path": "/tmp/file.txt"},
    ],
)
def test_upload_arguments_accept_only_frozen_asset_identity(arguments):
    raw = flow(step("upload_file", arguments, target_id="button"))
    raw["parameters"] = [
        dict(name=name, type="string", required=True) for name in ["asset", "digest"]
    ]
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize(
    "arguments", [{"path": "/tmp/result"}, {"url": "https://fixture.example/file"}]
)
def test_downloads_cannot_select_filesystem_paths_or_urls(arguments):
    with pytest.raises(ValueError):
        validate_definition(flow(step("download_file", arguments, target_id="button")))
