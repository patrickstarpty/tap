"""Validate browser-issued frame/document evidence, never model-provided identity."""

from typing import Any


def document_map(observation: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = observation.get("documents")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        return {}
    result = {}
    for entry in raw:
        if not isinstance(entry, dict) or any(
            not isinstance(entry.get(key), str) or not entry[key] or len(entry[key]) > limit
            for key, limit in (("frame_id", 200), ("document_id", 200), ("url", 2048))
        ):
            return {}
        if entry["frame_id"] in result:
            return {}
        result[entry["frame_id"]] = entry
    return result


def same_document(proof: dict[str, Any], documents: dict[str, dict[str, str]]) -> bool:
    frame = documents.get(str(proof.get("source_frame_id", "")))
    return frame is not None and proof.get("source_document_id") == frame["document_id"]
