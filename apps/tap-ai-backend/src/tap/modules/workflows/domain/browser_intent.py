"""Resolve append-only browser routing facts without mutating admitted input."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def effective_request(request: dict[str, Any], events: Iterable[Any]) -> dict[str, Any]:
    if request.get("kind") == "browser":
        return request
    for event in events:
        kind = event.get("kind") if isinstance(event, Mapping) else event.kind
        payload = event.get("payload", {}) if isinstance(event, Mapping) else event.payload
        if kind != "task.route_decided" or not isinstance(payload.get("browser_intent"), dict):
            continue
        return {
            **request,
            "kind": "browser",
            "browser": payload["browser_intent"],
            "model_alias": payload["browser_model_alias"],
        }
    return request
