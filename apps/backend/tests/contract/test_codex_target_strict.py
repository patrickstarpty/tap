from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from tap.modules.knowledge.adapters import codex_target
from tap.modules.knowledge.adapters.codex_target import (
    SUPPORTED_CODEX_CLI_VERSIONS,
    CodexTargetRejected,
    assert_target_unchanged,
    resolve_native_codex_target,
)


@dataclass(frozen=True, slots=True)
class FakeCodexInstall:
    command: Path
    javascript: Path
    native: Path
    package_root: Path
    platform_package_root: Path


def fake_codex_install(
    tmp_path: Path,
    *,
    package: str = "@openai/codex-darwin-arm64",
    triple: str = "aarch64-apple-darwin",
    native_magic: bytes = b"\xcf\xfa\xed\xfe",
    version: str = "0.149.0",
    launcher: bytes = b"#!/usr/bin/env node\n",
) -> FakeCodexInstall:
    package_root = (
        tmp_path
        / "nvm"
        / "versions"
        / "node"
        / "v24.0.0"
        / "lib"
        / "node_modules"
        / "@openai"
        / "codex"
    )
    javascript = package_root / "bin" / "codex.js"
    javascript.parent.mkdir(parents=True)
    javascript.write_bytes(launcher)
    javascript.chmod(0o755)
    (package_root / "package.json").write_text(
        json.dumps({"name": "@openai/codex", "version": version}),
        encoding="utf-8",
    )

    platform_package_root = package_root / "node_modules" / package
    native = platform_package_root / "vendor" / triple / "codex" / "codex"
    native.parent.mkdir(parents=True)
    native.write_bytes(native_magic + b"\x00" * 28)
    native.chmod(0o755)
    (platform_package_root / "package.json").write_text(
        json.dumps({"name": package, "version": version}),
        encoding="utf-8",
    )
    return FakeCodexInstall(
        command=javascript,
        javascript=javascript,
        native=native,
        package_root=package_root,
        platform_package_root=platform_package_root,
    )


def resolve_darwin(tree: FakeCodexInstall):  # type: ignore[no-untyped-def]
    return resolve_native_codex_target(
        tree.command,
        system="Darwin",
        machine="arm64",
        expected_version="0.149.0",
        uid=os.getuid(),
    )


def test_supported_versions_remain_closed_until_real_conformance() -> None:
    assert SUPPORTED_CODEX_CLI_VERSIONS == frozenset()


def test_nvm_js_launcher_resolves_to_same_package_native_binary(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    tree = fake_codex_install(
        tmp_path,
        package="@openai/codex-darwin-arm64",
        triple="aarch64-apple-darwin",
        native_magic=b"\xcf\xfa\xed\xfe",
    )

    def unexpected_execution(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("resolver executed a launcher")

    monkeypatch.setattr(subprocess, "run", unexpected_execution)
    target = resolve_darwin(tree)

    assert target.executable == tree.native.resolve()
    assert target.executable != tree.javascript.resolve()
    assert target.install_root == tree.package_root.resolve()
    assert target.version == "0.149.0"
    assert target.identity.device == tree.native.stat().st_dev
    assert target.identity.inode == tree.native.stat().st_ino
    assert target.identity.size == tree.native.stat().st_size
    assert target.identity.mtime_ns == tree.native.stat().st_mtime_ns


def test_direct_native_target_is_permitted_only_as_the_execution_target(tmp_path: Path) -> None:
    native = tmp_path / "standalone" / "codex"
    native.parent.mkdir()
    native.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 28)
    native.chmod(0o755)

    target = resolve_native_codex_target(
        native,
        system="Darwin",
        machine="arm64",
        expected_version="0.149.0",
        uid=os.getuid(),
    )

    assert target.executable == native.resolve()
    assert target.install_root == native.parent.resolve()
    assert target.version == "0.149.0"


def test_target_identity_change_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    target = resolve_darwin(tree)
    tree.native.write_bytes(tree.native.read_bytes() + b"changed")

    with pytest.raises(CodexTargetRejected, match="identity changed"):
        assert_target_unchanged(target)


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Windows", "AMD64"), ("Darwin", "x86_64"), ("Linux", "aarch64")],
)
def test_unsupported_platform_fails_closed(tmp_path: Path, system: str, machine: str) -> None:
    tree = fake_codex_install(tmp_path)

    with pytest.raises(CodexTargetRejected, match="unsupported platform"):
        resolve_native_codex_target(
            tree.command,
            system=system,
            machine=machine,
            expected_version="0.149.0",
            uid=os.getuid(),
        )


def test_unsupported_platform_package_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path, package="@openai/codex-darwin-x64")

    with pytest.raises(CodexTargetRejected, match="package"):
        resolve_darwin(tree)


def test_unsupported_vendor_triple_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path, triple="x86_64-apple-darwin")

    with pytest.raises(CodexTargetRejected, match="triple"):
        resolve_darwin(tree)


def test_package_version_must_match_the_explicit_expected_version(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path, version="0.148.0")

    with pytest.raises(CodexTargetRejected, match="version"):
        resolve_darwin(tree)


def test_only_the_exact_openai_codex_package_launcher_is_accepted(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    (tree.package_root / "package.json").write_text(
        json.dumps({"name": "attacker/codex", "version": "0.149.0"}),
        encoding="utf-8",
    )

    with pytest.raises(CodexTargetRejected, match="package"):
        resolve_darwin(tree)


def test_unrecognized_javascript_launcher_content_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path, launcher=b"console.log('not codex');\n")

    with pytest.raises(CodexTargetRejected, match="launcher"):
        resolve_darwin(tree)


def test_direct_script_target_fails_closed(tmp_path: Path) -> None:
    script = tmp_path / "codex"
    script.write_bytes(b"#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    with pytest.raises(CodexTargetRejected, match="native"):
        resolve_native_codex_target(
            script,
            system="Darwin",
            machine="arm64",
            expected_version="0.149.0",
            uid=os.getuid(),
        )


def test_command_symlink_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    command = tmp_path / "codex"
    command.symlink_to(tree.javascript)

    with pytest.raises(CodexTargetRejected, match="symlink"):
        resolve_native_codex_target(
            command,
            system="Darwin",
            machine="arm64",
            expected_version="0.149.0",
            uid=os.getuid(),
        )


def test_native_symlink_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    real_native = tree.native.with_name("codex-real")
    tree.native.rename(real_native)
    tree.native.symlink_to(real_native)

    with pytest.raises(CodexTargetRejected, match="symlink"):
        resolve_darwin(tree)


def test_native_path_outside_install_root_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    vendor = tree.platform_package_root / "vendor"
    outside_vendor = tmp_path / "outside-vendor"
    shutil.move(str(vendor), outside_vendor)
    vendor.symlink_to(outside_vendor, target_is_directory=True)

    with pytest.raises(CodexTargetRejected, match="symlink|escape"):
        resolve_darwin(tree)


@pytest.mark.parametrize("component", ["target", "package_root"])
def test_group_or_world_writable_target_or_component_fails_closed(
    tmp_path: Path, component: str
) -> None:
    tree = fake_codex_install(tmp_path)
    unsafe_path = tree.native if component == "target" else tree.package_root
    unsafe_path.chmod(0o775)

    with pytest.raises(CodexTargetRejected, match="group/world-writable"):
        resolve_darwin(tree)


def test_non_root_non_current_component_owner_fails_closed(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tree = fake_codex_install(tmp_path)
    real_lstat = codex_target._lstat
    rejected_component = tree.package_root.resolve()

    def lstat_with_untrusted_owner(path: Path):  # type: ignore[no-untyped-def]
        result = real_lstat(path)
        if path == rejected_component:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_uid=os.getuid() + 1,
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    monkeypatch.setattr(codex_target, "_lstat", lstat_with_untrusted_owner)

    with pytest.raises(CodexTargetRejected, match="owner"):
        resolve_darwin(tree)


@pytest.mark.parametrize("magic", [b"MZ\x90\x00", b"\x7fELF", b"\xfe\xed\xfa\xcf"])
def test_wrong_native_magic_fails_closed(tmp_path: Path, magic: bytes) -> None:
    tree = fake_codex_install(tmp_path, native_magic=magic)

    with pytest.raises(CodexTargetRejected, match="magic"):
        resolve_darwin(tree)


def test_linux_elf_target_uses_the_exact_package_and_triple(tmp_path: Path) -> None:
    tree = fake_codex_install(
        tmp_path,
        package="@openai/codex-linux-x64",
        triple="x86_64-unknown-linux-musl",
        native_magic=b"\x7fELF",
    )

    target = resolve_native_codex_target(
        tree.command,
        system="Linux",
        machine="x86_64",
        expected_version="0.149.0",
        uid=os.getuid(),
    )

    assert target.executable == tree.native.resolve()
    assert target.install_root == tree.package_root.resolve()


def test_missing_execute_bit_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    tree.native.chmod(0o644)

    with pytest.raises(CodexTargetRejected, match="executable"):
        resolve_darwin(tree)


def test_missing_native_target_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    tree.native.unlink()

    with pytest.raises(CodexTargetRejected, match="missing"):
        resolve_darwin(tree)


def test_nonregular_native_target_fails_closed(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    tree.native.unlink()
    tree.native.mkdir()
    tree.native.chmod(0o755)

    with pytest.raises(CodexTargetRejected, match="regular"):
        resolve_darwin(tree)


def test_revalidation_repeats_permissions_and_magic_checks(tmp_path: Path) -> None:
    tree = fake_codex_install(tmp_path)
    target = resolve_darwin(tree)
    tree.native.chmod(stat.S_IMODE(tree.native.stat().st_mode) | stat.S_IWGRP)

    with pytest.raises(CodexTargetRejected, match="group/world-writable"):
        assert_target_unchanged(target)
