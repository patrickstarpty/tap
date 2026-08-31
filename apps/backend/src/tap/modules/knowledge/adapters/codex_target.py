"""Fail-closed resolution of a native Codex CLI execution target."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SUPPORTED_CODEX_CLI_VERSIONS: frozenset[str] = frozenset()

_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_NODE_SHEBANG = b"#!/usr/bin/env node\n"
_MAX_PACKAGE_METADATA_BYTES = 64 * 1024
_NATIVE_HEADER_BYTES = 20
_RESOLVER_CODEX_VERSION = "0.149.0"
_NPM_GLOBAL_LINK_TARGET = "../lib/node_modules/@openai/codex/bin/codex.js"


class CodexTargetRejected(RuntimeError):
    """The candidate cannot be trusted as a native Codex execution target."""


@dataclass(frozen=True, slots=True)
class NativeTargetIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class NativeTargetHeader:
    format: Literal["mach-o", "elf"]
    magic: bytes
    bits: int
    byteorder: Literal["little", "big"]
    machine: int


@dataclass(frozen=True, slots=True)
class NativeCodexTarget:
    executable: Path
    install_root: Path
    version: str
    identity: NativeTargetIdentity
    header: NativeTargetHeader


_MACHO_ARM64_HEADER = NativeTargetHeader(
    format="mach-o",
    magic=b"\xcf\xfa\xed\xfe",
    bits=64,
    byteorder="little",
    machine=0x0100000C,
)
_ELF_X86_64_HEADER = NativeTargetHeader(
    format="elf",
    magic=b"\x7fELF",
    bits=64,
    byteorder="little",
    machine=62,
)
_PLATFORM_TARGETS = {
    ("Darwin", "arm64"): (
        "@openai/codex-darwin-arm64",
        "aarch64-apple-darwin",
        "0.149.0-darwin-arm64",
        _MACHO_ARM64_HEADER,
    ),
    ("Linux", "x86_64"): (
        "@openai/codex-linux-x64",
        "x86_64-unknown-linux-musl",
        "0.149.0-linux-x64",
        _ELF_X86_64_HEADER,
    ),
}
_TRUSTED_HEADERS = frozenset(target[3] for target in _PLATFORM_TARGETS.values())


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
    if (
        not isinstance(expected_version, str)
        or _VERSION.fullmatch(expected_version) is None
        or expected_version != _RESOLVER_CODEX_VERSION
    ):
        raise CodexTargetRejected("expected Codex version is not canonical")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise CodexTargetRejected("current uid is invalid")

    package, triple, platform_version, expected_header = platform_target
    command = _validated_command_path(command_path, uid=uid)
    command_stat = _lstat(command)
    if _read_prefix(command, len(expected_header.magic)) == expected_header.magic:
        native_stat = _validate_native_executable(
            command,
            uid=uid,
            expected_header=expected_header,
            checked_stat=command_stat,
        )
        return _target(
            executable=command,
            install_root=command.parent,
            version=expected_version,
            native_stat=native_stat,
            header=expected_header,
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
        expected_name="@openai/codex",
        expected_version=platform_version,
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

    native = triple_root / "bin" / "codex"
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
        expected_header=expected_header,
    )
    return _target(
        executable=native,
        install_root=package_root,
        version=expected_version,
        native_stat=native_stat,
        header=expected_header,
    )


def assert_target_unchanged(target: NativeCodexTarget) -> None:
    """Revalidate a target's trust policy and immutable identity immediately before use."""

    if not isinstance(target, NativeCodexTarget):
        raise TypeError("target must be a NativeCodexTarget")
    if target.header not in _TRUSTED_HEADERS:
        raise CodexTargetRejected("native Codex target header contract is unsupported")
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
    initial_stat = _validate_native_executable(
        executable,
        uid=uid,
        expected_header=target.header,
    )
    final_install_root = _validated_resolved_path(
        target.install_root,
        uid=uid,
        label="install root",
    )
    final_executable = _validated_resolved_path(
        target.executable,
        uid=uid,
        label="native target",
    )
    if (
        final_install_root != install_root
        or final_executable != executable
        or not final_executable.is_relative_to(final_install_root)
    ):
        raise CodexTargetRejected("native Codex target containment changed")
    final_stat = _validate_native_executable(
        final_executable,
        uid=uid,
        expected_header=target.header,
    )
    if _identity(initial_stat) != _identity(final_stat) or _identity(final_stat) != target.identity:
        raise CodexTargetRejected("native Codex target identity changed")


def _target(
    *,
    executable: Path,
    install_root: Path,
    version: str,
    native_stat: os.stat_result,
    header: NativeTargetHeader,
) -> NativeCodexTarget:
    return NativeCodexTarget(
        executable=executable,
        install_root=install_root,
        version=version,
        identity=_identity(native_stat),
        header=header,
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
    expected_header: NativeTargetHeader,
    checked_stat: os.stat_result | None = None,
) -> os.stat_result:
    path_stat = checked_stat or _lstat(path)
    _validate_owner_and_mode(path, path_stat, uid=uid)
    _validate_regular_executable(path, path_stat)
    header = _read_prefix(path, _NATIVE_HEADER_BYTES)
    _validate_native_header(header, expected_header)
    final_stat = _lstat(path)
    _validate_owner_and_mode(path, final_stat, uid=uid)
    _validate_regular_executable(path, final_stat)
    if _identity(final_stat) != _identity(path_stat):
        raise CodexTargetRejected("native Codex target identity changed during validation")
    return final_stat


def _validate_native_header(header: bytes, expected: NativeTargetHeader) -> None:
    if len(header) < _NATIVE_HEADER_BYTES:
        raise CodexTargetRejected("native Codex target architecture/header is incomplete")
    if header[:4] != expected.magic:
        raise CodexTargetRejected("native Codex target has unsupported magic")
    if expected.format == "mach-o":
        machine = int.from_bytes(header[4:8], byteorder=expected.byteorder)
        if expected.bits != 64 or machine != expected.machine:
            raise CodexTargetRejected("native Codex target architecture/header is unsupported")
        return
    if expected.format == "elf":
        expected_class = {32: 1, 64: 2}.get(expected.bits)
        expected_data = {"little": 1, "big": 2}.get(expected.byteorder)
        if expected_class is None or expected_data is None:
            raise CodexTargetRejected("native Codex target header contract is unsupported")
        if header[4] != expected_class or header[5] != expected_data or header[6] != 1:
            raise CodexTargetRejected("native Codex target architecture/header is unsupported")
        machine = int.from_bytes(header[18:20], byteorder=expected.byteorder)
        if machine != expected.machine:
            raise CodexTargetRejected("native Codex target architecture/header is unsupported")
        return
    raise CodexTargetRejected("native Codex target header contract is unsupported")


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


def _validated_command_path(path: Path, *, uid: int) -> Path:
    lexical = _absolute_lexical_path(path)
    if ".." in lexical.parts:
        raise CodexTargetRejected("command contains a path escape")
    try:
        command_stat = _lstat(lexical)
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise CodexTargetRejected("command is missing") from error
    if not stat.S_ISLNK(command_stat.st_mode):
        return _validated_resolved_path(lexical, uid=uid, label="command")
    if lexical.name != "codex" or lexical.parent.name != "bin":
        raise CodexTargetRejected("command symlink shape is unsupported")

    parent = _validated_resolved_path(lexical.parent, uid=uid, label="command parent")
    _validate_owner(lexical, command_stat, uid=uid)
    try:
        link_target = os.readlink(lexical)
    except OSError as error:
        raise CodexTargetRejected("command symlink is unreadable") from error
    if link_target != _NPM_GLOBAL_LINK_TARGET:
        raise CodexTargetRejected("command symlink target is unsupported")

    expected = parent.parent / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    target = _validated_resolved_path(expected, uid=uid, label="command symlink target")
    try:
        resolved_from_link = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CodexTargetRejected("command symlink target is missing") from error
    if resolved_from_link != target:
        raise CodexTargetRejected("command symlink escaped its canonical package")

    final_parent = _validated_resolved_path(lexical.parent, uid=uid, label="command parent")
    final_stat = _lstat(lexical)
    _validate_owner(lexical, final_stat, uid=uid)
    try:
        final_link_target = os.readlink(lexical)
        final_resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CodexTargetRejected("command symlink identity changed") from error
    if (
        final_parent != parent
        or not stat.S_ISLNK(final_stat.st_mode)
        or _identity(final_stat) != _identity(command_stat)
        or final_link_target != link_target
        or final_resolved != target
    ):
        raise CodexTargetRejected("command symlink identity changed")
    return target


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
    _validate_owner(path, path_stat, uid=uid)
    if path_stat.st_mode & 0o022:
        raise CodexTargetRejected(f"{path} is group/world-writable")


def _validate_owner(path: Path, path_stat: os.stat_result, *, uid: int) -> None:
    if path_stat.st_uid not in {0, uid}:
        raise CodexTargetRejected(f"{path} has an untrusted owner")


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
