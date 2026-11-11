"""Bounded immutable user-selected upload assets, no paths or URL fetching."""

import base64
import binascii
import re
from typing import Any

from .definition import DebugDefinition

MAX_ASSET_BYTES = 256_000
MAX_FROZEN_INPUT_BYTES = 1_000_000
MAX_DOWNLOAD_BYTES = 5_000_000


def safe_filename(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ValueError("Invalid filename")
    name = value.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".")[:160]
    return name or "download.bin"


def asset_content_type(filename: str) -> str:
    # Browser file-type metadata, not content verification or execution permission.
    return {
        "txt": "text/plain",
        "csv": "text/csv",
        "json": "application/json",
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "xml": "application/xml",
        "zip": "application/zip",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(filename.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def decode_asset(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((MAX_ASSET_BYTES + 2) // 3):
        raise ValueError("Upload exceeds 256000 bytes")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid upload bytes") from None
    if not data or len(data) > MAX_ASSET_BYTES:
        raise ValueError("Invalid upload size")
    return data


def public_asset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "filename",
            "content_type",
            "sha256",
            "size_bytes",
            "created_at",
        )
    }


def upload_references(definition: DebugDefinition) -> list[dict[str, Any]]:
    return [
        step.arguments for step in definition.steps if step.operation == "upload_file"
    ]
