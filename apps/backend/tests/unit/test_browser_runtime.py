import hashlib
from pathlib import Path

import pytest

from tap_platform.browser_automation.contracts import BrowserBundle, BrowserLimits
from tap_platform.browser_automation.docker import (
    BrowserRuntime,
    SubprocessDockerClient,
)
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


def test_browser_evidence_default_handles_bounded_page_trace() -> None:
    assert BrowserLimits().max_output_bytes == 5_000_000
    assert BrowserLimits(max_output_bytes=1_000_000).max_output_bytes == 1_000_000
    with pytest.raises(ValueError, match="browser output limit"):
        BrowserLimits(max_output_bytes=10_000_001)


@pytest.mark.asyncio
async def test_evidence_overflow_has_distinct_failure_receipt(monkeypatch) -> None:
    script = b"import { test } from '@playwright/test'; test('ok', () => {});"
    bundle = BrowserBundle(
        "b1",
        "p1",
        hashlib.sha256(script).hexdigest(),
        script,
        hashlib.sha256(b"{}").hexdigest(),
        ("https://example.com",),
        "2026-11-07T00:00:00Z",
    )
    runtime = BrowserRuntime(
        SubprocessDockerClient(),
        "browser:test",
        evidence_sink=object(),
        project_id="p1",
    )

    async def overflow(*_args, **_kwargs):
        raise RuntimeError("browser evidence exceeds limit")

    monkeypatch.setattr(runtime, "_execute_work", overflow)
    result = await runtime.execute(
        bundle, {"task_id": "t1", "input_data": {}}, BrowserLimits(), execution_id="e1"
    )
    assert result.status == "failed"
    assert result.failure_code == "evidence_limit"
    assert result.result == {}
    assert result.evidence == ()


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


def test_public_navigation_accepts_external_site_but_never_private_destinations():
    from tap_platform.browser_automation.policy import validate_url

    assert (
        validate_url(
            "https://api.example.net/path",
            ("https://example.com",),
            network_mode="public_web",
        )
        == "https://api.example.net/path"
    )
    with pytest.raises(ValueError):
        validate_url("https://api.example.net/path", ("https://example.com",))
    for url in (
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://10.0.0.1",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            validate_url(url, (), network_mode="public_web")
