"""Freeze one approved OpenCode roster and one LiteLLM provider for a coding task."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_MODEL = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
_SPECIALISTS = frozenset({"sde", "sdet", "qae"})


def _agent(value: object, expected: str) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("agent_id") != expected:
        raise ValueError("invalid frozen specialist identity")
    fields = ("revision_id", "instruction", "description")
    if any(not isinstance(value.get(name), str) or not value[name] for name in fields):
        raise ValueError("frozen specialist content unavailable")
    if len(value["instruction"].encode()) > 12000:
        raise ValueError("frozen specialist instruction too large")
    return {name: value[name] for name in fields}


def build_config(
    request: dict[str, Any], shim_origin: str, shim_token: str, *, output_tokens: int
) -> dict[str, Any]:
    """Build an inline config with no inherited provider or writable permissions."""
    parsed = urlsplit(shim_origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not shim_token
        or not 512 <= output_tokens <= 8192
    ):
        raise ValueError("invalid fixed OpenCode model endpoint")
    alias = request.get("model_alias")
    if not isinstance(alias, str) or not _MODEL.fullmatch(alias):
        raise ValueError("frozen model alias invalid")
    model = f"litellm/{alias}"
    root = _agent(request.get("root"), "orchestrator")
    requested = request.get("requested_ids", [])
    candidates = request.get("candidates", [])
    if (
        not isinstance(requested, list)
        or not isinstance(candidates, list)
        or len(requested) != len(set(requested))
        or not set(requested) <= _SPECIALISTS
    ):
        raise ValueError("unknown frozen specialist")
    indexed = {item.get("agent_id"): item for item in candidates if isinstance(item, dict)}
    if any(item not in indexed for item in requested):
        raise ValueError("selected specialist is missing")
    agent: dict[str, Any] = {
        name: {"disable": True} for name in ("build", "plan", "general", "explore")
    }
    agent.update({name: {"model": model} for name in ("compaction", "summary", "title")})
    shared = {"*": "deny", "read": "allow", "glob": "allow", "grep": "allow"}
    delegation = {"*": "deny", **{name: "allow" for name in requested}}
    agent["orchestrator"] = {
        "mode": "primary",
        "model": model,
        "prompt": root["instruction"],
        "permission": {**shared, "edit": "allow", "bash": "allow", "task": delegation},
    }
    for name in requested:
        frozen = _agent(indexed[name], name)
        writable = name in {"sde", "sdet"}
        agent[name] = {
            "mode": "subagent",
            "model": model,
            "description": frozen["description"],
            "prompt": frozen["instruction"],
            "permission": {
                **shared,
                "edit": "allow" if writable else "deny",
                "bash": "allow" if writable else "deny",
                "task": "deny",
            },
        }
    return {
        "autoupdate": False,
        "plugin": [],
        "enabled_providers": ["litellm"],
        "model": model,
        "small_model": model,
        "default_agent": "orchestrator",
        "tools": {
            name: False
            for name in ("webfetch", "websearch", "question", "todowrite", "skill", "lsp")
        },
        "agent": agent,
        "provider": {
            "litellm": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": shim_origin + "/v1", "apiKey": shim_token},
                "models": {
                    alias: {
                        "name": alias,
                        "limit": {"context": 129024, "input": 129024, "output": output_tokens},
                    }
                },
            }
        },
    }
