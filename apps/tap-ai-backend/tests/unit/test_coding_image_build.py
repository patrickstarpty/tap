from pathlib import Path

import pytest
from scripts.build_coding_image import validate_binary, validate_ripgrep_archive


def test_pinned_binary_rejects_untrusted_bytes(tmp_path: Path):
    path = tmp_path / "opencode"
    path.write_bytes(b"not the pinned binary")
    with pytest.raises(ValueError, match="digest"):
        validate_binary(path)


def test_pinned_ripgrep_archive_rejects_untrusted_bytes(tmp_path: Path):
    path = tmp_path / "ripgrep.tar.gz"
    path.write_bytes(b"not the pinned archive")
    with pytest.raises(ValueError, match="digest"):
        validate_ripgrep_archive(path)


def test_browser_image_preflight_rejects_stale_control_script(tmp_path, monkeypatch):
    import subprocess

    from scripts.build_coding_image import verify_browser_image_sources

    browser = tmp_path / "deploy/browser"
    browser.mkdir(parents=True)
    (browser / "ctl.mjs").write_text("current timeout")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "0" * 64 + "  /opt/browser/ctl.mjs\n", ""
        ),
    )
    with pytest.raises(ValueError, match="ctl.mjs"):
        verify_browser_image_sources(tmp_path, "local-browser")


def test_browser_image_preflight_accepts_matching_source(tmp_path, monkeypatch):
    import hashlib
    import subprocess

    from scripts.build_coding_image import verify_browser_image_sources

    browser = tmp_path / "deploy/browser"
    browser.mkdir(parents=True)
    source = b"current timeout"
    (browser / "ctl.mjs").write_bytes(source)

    def inspect(argv, **kwargs):
        assert "--network=none" in argv and "--read-only" in argv and "--rm" in argv
        return subprocess.CompletedProcess(
            argv, 0, hashlib.sha256(source).hexdigest() + "  /opt/browser/ctl.mjs\n", ""
        )

    monkeypatch.setattr(subprocess, "run", inspect)
    verify_browser_image_sources(tmp_path, "local-browser")


def test_browser_image_preflight_cleans_owned_container_after_timeout(tmp_path, monkeypatch):
    import subprocess

    from scripts.build_coding_image import verify_browser_image_sources

    browser = tmp_path / "deploy/browser"
    browser.mkdir(parents=True)
    (browser / "ctl.mjs").write_text("current")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 30)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        verify_browser_image_sources(tmp_path, "local-browser")
    assert len(commands) == 2
    name = commands[0][commands[0].index("--name") + 1]
    assert name.startswith("tapper-image-check-")
    assert commands[1] == ["docker", "rm", "-f", name]
