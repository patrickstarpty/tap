import hashlib
from pathlib import Path

import pytest

from tap_platform.browser_automation.contracts import BrowserBundle
from tap_platform.browser_automation.policy import (
    execution_succeeded,
    validate_origin,
    validate_evidence_path,
)


def test_skipped_or_missing_evidence_is_not_success() -> None:
    assert not execution_succeeded(0, 0, 1, True)
    assert not execution_succeeded(0, 1, 0, False)
    assert not execution_succeeded(1, 1, 0, True)
    assert execution_succeeded(0, 1, 0, True)


@pytest.mark.parametrize(
    "origin",
    [
        "file:///tmp/a",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "http://[::1]",
        "https://user:pass@example.com",
    ],
)
def test_untrusted_origin_rejected(origin: str) -> None:
    with pytest.raises(ValueError):
        validate_origin(origin)


def test_bundle_requires_single_frozen_entry_and_digest() -> None:
    script = (
        b"import { test } from '@playwright/test';\ntest('ok', async ({page}) => {});"
    )
    bundle = BrowserBundle(
        bundle_id="b1",
        project_id="p1",
        sha256=hashlib.sha256(script).hexdigest(),
        script_bytes=script,
        input_sha256="0" * 64,
        allowed_origins=("https://example.com",),
        created_at="2026-11-07T00:00:00Z",
    )
    assert bundle.entry_name == "flow.spec.ts"
    with pytest.raises(ValueError):
        BrowserBundle(
            bundle_id="b1",
            project_id="p1",
            sha256="0" * 64,
            script_bytes=script,
            input_sha256="0" * 64,
            allowed_origins=("https://example.com",),
            created_at="2026-11-07T00:00:00Z",
        )


def test_evidence_path_cannot_escape_or_follow_symlink(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("secret")
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        validate_evidence_path(root, "../outside")
    with pytest.raises(ValueError):
        validate_evidence_path(root, "link")
    assert validate_evidence_path(root, "capture.png") == root / "capture.png"


def test_evidence_root_may_have_symlink_ancestor(tmp_path: Path) -> None:
    real = tmp_path / "real"
    root = real / "evidence"
    root.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    assert (
        validate_evidence_path(alias / "evidence", "capture.png")
        == alias / "evidence" / "capture.png"
    )
