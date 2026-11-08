"""Validation of untrusted browser inputs and trusted execution receipts."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit


def execution_succeeded(
    exit_code: int, passed: int, skipped: int, evidence_complete: bool
) -> bool:
    return exit_code == 0 and passed > 0 and skipped == 0 and evidence_complete


def validate_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid browser origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("private browser origin") from None
    else:
        if not address.is_global:
            raise ValueError("private browser origin")
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid origin port")
    return f"{parsed.scheme}://{parsed.hostname.lower()}{f':{port}' if port else ''}"


def validate_network_mode(mode: str) -> str:
    if mode not in {"declared_origins", "public_web"}:
        raise ValueError("invalid browser network mode")
    return mode


def validate_url(
    url: str,
    allowed_origins: tuple[str, ...],
    *,
    network_mode: str = "declared_origins",
) -> str:
    validate_network_mode(network_mode)
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials forbidden")
    origin = validate_origin(f"{parsed.scheme}://{parsed.netloc}")
    if network_mode == "declared_origins" and origin not in allowed_origins:
        raise ValueError("origin not allowed")
    return url


def validate_evidence_path(root: Path, name: str) -> Path:
    candidate = Path(name)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"..", "."} for part in candidate.parts)
    ):
        raise ValueError("invalid evidence path")
    path = root / candidate
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink evidence path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes root")
    return path
