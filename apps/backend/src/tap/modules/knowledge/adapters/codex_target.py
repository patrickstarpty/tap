"""Fail-closed resolution of a native Codex CLI execution target."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_CODEX_CLI_VERSIONS: frozenset[str] = frozenset()

_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_PLATFORM_TARGETS = {
    ("Darwin", "arm64"): (
        "@openai/codex-darwin-arm64",
        "aarch64-apple-darwin",
        b"\xcf\xfa\xed\xfe",
    ),
    ("Linux", "x86_64"): (
        "@openai/codex-linux-x64",
        "x86_64-unknown-linux-musl",
        b"\x7fELF",
    ),
}
_NATIVE_MAGICS = frozenset(target[2] for target in _PLATFORM_TARGETS.values())
_NODE_SHEBANG = b"#!/usr/bin/env node\n"
_MAX_PACKAGE_METADATA_BYTES = 64 * 1024


class CodexTargetRejected(RuntimeError):
    """The candidate cannot be trusted as a native Codex execution target."""


@dataclass(frozen=True, slots=True)
class NativeTargetIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class NativeCodexTarget:
    executable: Path
    install_root: Path
    version: str
    identity: NativeTargetIdentity


def resolve_native_codex_target(
    command_path: Path,
    *,
    system: str,
    machine: str,
    expected_version: str,
    uid: int,
) -> NativeCodexTarget:
    """Resolve one statically trusted native target without executing the candidate."""

    platform_target = _PLATFORM_TARGETS.get((system, machine))
    if platform_target is None:
        raise CodexTargetRejected("unsupported platform for the local Codex target")
    if not isinstance(expected_version, str) or _VERSION.fullmatch(expected_version) is None:
        raise CodexTargetRejected("expected Codex version is not canonical")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise CodexTargetRejected("current uid is invalid")

    package, triple, expected_magic = platform_target
    command = _validated_resolved_path(command_path, uid=uid, label="command")
    command_stat = _lstat(command)
    if _read_prefix(command, len(expected_magic)) == expected_magic:
        native_stat = _validate_native_executable(
            command,
            uid=uid,
            expected_magics=frozenset({expected_magic}),
            checked_stat=command_stat,
        )
        return _target(
            executable=command,
            install_root=command.parent,
            version=expected_version,
            native_stat=native_stat,
        )

    package_root = _launcher_package_root(command)
    _validate_regular_executable(command, command_stat)
    if _read_first_line(command) != _NODE_SHEBANG:
        raise CodexTargetRejected("Codex JavaScript launcher content is unsupported")

    _validate_package_metadata(
        package_root / "package.json",
        expected_name="@openai/codex",
        expected_version=expected_version,
        uid=uid,
    )

    package_parent = package_root / "node_modules" / "@openai"
    try:
        _validated_resolved_path(package_parent, uid=uid, label="platform package parent")
    except CodexTargetRejected as error:
        raise CodexTargetRejected("unsupported Codex platform package") from error
    platform_package_root = package_root / "node_modules" / package
    try:
        platform_package_root = _validated_resolved_path(
            platform_package_root,
            uid=uid,
            label="platform package",
        )
    except CodexTargetRejected as error:
        raise CodexTargetRejected("unsupported Codex platform package") from error
    if not platform_package_root.is_relative_to(package_root):
        raise CodexTargetRejected("Codex platform package path escaped the install root")
    _validate_package_metadata(
        platform_package_root / "package.json",
        expected_name=package,
        expected_version=expected_version,
        uid=uid,
    )

    vendor_root = platform_package_root / "vendor"
    _validated_resolved_path(vendor_root, uid=uid, label="vendor root")
    triple_root = vendor_root / triple
    try:
        triple_root = _validated_resolved_path(
            triple_root,
            uid=uid,
            label="vendor triple",
        )
    except CodexTargetRejected as error:
        raise CodexTargetRejected("unsupported Codex vendor triple") from error
    if not triple_root.is_relative_to(package_root):
        raise CodexTargetRejected("Codex vendor triple path escaped the install root")

    native = triple_root / "codex" / "codex"
    try:
        native = _validated_resolved_path(native, uid=uid, label="native target")
    except CodexTargetRejected as error:
        if "missing" in str(error):
            raise CodexTargetRejected("native Codex target is missing") from error
        raise
    if not native.is_relative_to(package_root):
        raise CodexTargetRejected("native Codex target path escaped the install root")
    native_stat = _validate_native_executable(
        native,
        uid=uid,
        expected_magics=frozenset({expected_magic}),
    )
    return _target(
        executable=native,
        install_root=package_root,
        version=expected_version,
        native_stat=native_stat,
    )


def assert_target_unchanged(target: NativeCodexTarget) -> None:
    """Revalidate a target's trust policy and immutable identity immediately before use."""

    if not isinstance(target, NativeCodexTarget):
        raise TypeError("target must be a NativeCodexTarget")
    uid = os.getuid()
    install_root = _validated_resolved_path(
        target.install_root,
        uid=uid,
        label="install root",
    )
    executable = _validated_resolved_path(
        target.executable,
        uid=uid,
        label="native target",
    )
    if not executable.is_relative_to(install_root):
        raise CodexTargetRejected("native Codex target path escaped the install root")
    native_stat = _validate_native_executable(
        executable,
        uid=uid,
        expected_magics=_NATIVE_MAGICS,
    )
    if _identity(native_stat) != target.identity:
        raise CodexTargetRejected("native Codex target identity changed")


def _target(
    *,
    executable: Path,
    install_root: Path,
    version: str,
    native_stat: os.stat_result,
) -> NativeCodexTarget:
    return NativeCodexTarget(
        executable=executable,
        install_root=install_root,
        version=version,
        identity=_identity(native_stat),
    )


def _identity(result: os.stat_result) -> NativeTargetIdentity:
    return NativeTargetIdentity(
        device=result.st_dev,
        inode=result.st_ino,
        size=result.st_size,
        mtime_ns=result.st_mtime_ns,
    )


def _launcher_package_root(command: Path) -> Path:
    if (
        command.name != "codex.js"
        or command.parent.name != "bin"
        or command.parent.parent.name != "codex"
        or command.parent.parent.parent.name != "@openai"
    ):
        raise CodexTargetRejected("command is not a trusted native Codex executable")
    return command.parent.parent


def _validate_package_metadata(
    path: Path,
    *,
    expected_name: str,
    expected_version: str,
    uid: int,
) -> None:
    metadata_path = _validated_resolved_path(path, uid=uid, label="package metadata")
    metadata_stat = _lstat(metadata_path)
    if not stat.S_ISREG(metadata_stat.st_mode):
        raise CodexTargetRejected("Codex package metadata is not a regular file")
    if not 0 < metadata_stat.st_size <= _MAX_PACKAGE_METADATA_BYTES:
        raise CodexTargetRejected("Codex package metadata size is invalid")
    try:
        raw = metadata_path.read_bytes()
        metadata: Any = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CodexTargetRejected("Codex package metadata is invalid") from error
    if not isinstance(metadata, dict) or metadata.get("name") != expected_name:
        raise CodexTargetRejected("Codex package name is unsupported")
    if metadata.get("version") != expected_version:
        raise CodexTargetRejected("Codex package version does not match the expected version")


def _validate_native_executable(
    path: Path,
    *,
    uid: int,
    expected_magics: frozenset[bytes],
    checked_stat: os.stat_result | None = None,
) -> os.stat_result:
    path_stat = checked_stat or _lstat(path)
    _validate_owner_and_mode(path, path_stat, uid=uid)
    _validate_regular_executable(path, path_stat)
    magic = _read_prefix(path, 4)
    if magic not in expected_magics:
        raise CodexTargetRejected("native Codex target has unsupported magic")
    final_stat = _lstat(path)
    if _identity(final_stat) != _identity(path_stat):
        raise CodexTargetRejected("native Codex target identity changed during validation")
    return final_stat


def _validate_regular_executable(path: Path, path_stat: os.stat_result) -> None:
    if not stat.S_ISREG(path_stat.st_mode):
        raise CodexTargetRejected(f"{path} is not a regular native target")
    if path_stat.st_mode & 0o111 == 0 or not os.access(path, os.X_OK):
        raise CodexTargetRejected(f"{path} is not executable")


def _validated_resolved_path(path: Path, *, uid: int, label: str) -> Path:
    lexical = _absolute_lexical_path(path)
    if ".." in lexical.parts:
        raise CodexTargetRejected(f"{label} contains a path escape")
    anchor = Path(lexical.anchor)
    current = anchor
    _validate_component(current, uid=uid, label=label)
    for part in lexical.parts[1:]:
        current = current / part
        _validate_component(current, uid=uid, label=label)
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CodexTargetRejected(f"{label} is missing") from error
    if resolved != lexical:
        raise CodexTargetRejected(f"{label} resolved through a symlink or path escape")
    return resolved


def _absolute_lexical_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def _validate_component(path: Path, *, uid: int, label: str) -> None:
    try:
        component_stat = _lstat(path)
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise CodexTargetRejected(f"{label} is missing") from error
    if stat.S_ISLNK(component_stat.st_mode):
        raise CodexTargetRejected(f"{label} contains a symlink")
    _validate_owner_and_mode(path, component_stat, uid=uid)


def _validate_owner_and_mode(path: Path, path_stat: os.stat_result, *, uid: int) -> None:
    if path_stat.st_uid not in {0, uid}:
        raise CodexTargetRejected(f"{path} has an untrusted owner")
    if path_stat.st_mode & 0o022:
        raise CodexTargetRejected(f"{path} is group/world-writable")


def _read_first_line(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.readline(256)
    except OSError as error:
        raise CodexTargetRejected("Codex JavaScript launcher is unreadable") from error


def _read_prefix(path: Path, length: int) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.read(length)
    except OSError as error:
        raise CodexTargetRejected("native Codex target is unreadable") from error


def _lstat(path: Path) -> os.stat_result:
    """Narrow test seam for ownership-policy coverage without privileged chown."""

    return path.stat(follow_symlinks=False)
