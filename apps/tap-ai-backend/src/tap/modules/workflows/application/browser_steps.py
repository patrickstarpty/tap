"""Persist a business plan before inspection and correlate actual browser actions."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Execute = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


def diagnostic_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        p = urlsplit(value)
        if p.scheme not in {"http", "https"} or not p.hostname:
            return ""
        host = p.hostname
        if ":" in host:
            host = f"[{host}]"
        return urlunsplit((p.scheme, host + (f":{p.port}" if p.port else ""), p.path, "", ""))[
            :2048
        ]
    except ValueError:
        return ""


class BrowserProgress:
    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self.steps: dict[str, dict[str, str]] = {}
        self.used: set[str] = set()
        self.revision = 0
        self.has_page_observation = False

    async def handle(
        self, payload: dict[str, Any], action_id: str, execute: Execute
    ) -> dict[str, Any]:
        if payload.get("type") == "plan":
            raw = payload.get("steps")
            if not isinstance(raw, list) or not 1 <= len(raw) <= 24:
                return {"error": "BDD plan requires 1 to 24 steps"}
            steps = {}
            for item in raw:
                if not isinstance(item, dict) or set(item) - {
                    "id",
                    "keyword",
                    "title",
                    "expected_result",
                }:
                    return {"error": "invalid BDD plan step"}
                ident, keyword, title = item.get("id"), item.get("keyword"), item.get("title")
                expected = item.get("expected_result", "")
                if (
                    not isinstance(ident, str)
                    or not _ID.fullmatch(ident)
                    or ident in steps
                    or keyword not in {"Given", "When", "Then", "And"}
                    or not isinstance(title, str)
                    or not 1 <= len(title.strip()) <= 200
                    or not isinstance(expected, str)
                    or len(expected) > 400
                ):
                    return {"error": "invalid BDD plan step"}
                steps[ident] = {
                    "id": ident,
                    "keyword": keyword,
                    "title": title.strip(),
                    "expected_result": expected,
                }
            if any(steps.get(ident) != self.steps[ident] for ident in self.used):
                return {"error": "BDD plan cannot rewrite or remove executed steps"}
            reason = payload.get("reason", "")
            if self.revision and (not isinstance(reason, str) or not reason.strip()):
                return {"error": "BDD plan revision requires a reason"}
            if not isinstance(reason, str) or len(reason) > 400:
                return {"error": "invalid plan revision reason"}
            await self.emit(
                "browser.plan_created",
                {"revision": self.revision + 1, "steps": list(steps.values()), "reason": reason},
            )
            self.steps = steps
            self.revision += 1
            return {"status": "planned", "revision": self.revision, "step_ids": list(steps)}
        if not self.steps:
            return {"error": "Create the BDD plan before browser actions"}
        step_id = payload.get("step")
        if not isinstance(step_id, str) or step_id not in self.steps:
            return {"error": "Browser action must reference a planned BDD step id"}
        action = {key: value for key, value in payload.items() if key != "step"}
        step = self.steps[step_id]
        details: dict[str, Any] = {
            "action_id": action_id,
            "operation": action.get("type"),
            "step_id": step_id,
            "step_keyword": step["keyword"],
            "step_title": step["title"],
            "expected_result": step["expected_result"],
            "revision": self.revision,
            "url": diagnostic_url(action.get("url")),
            "selector": str(action.get("selector", ""))[:1000],
            "input": "[hidden]" if "value" in action else str(action.get("key", ""))[:100],
        }
        await self.emit("browser.action_started", details)
        self.used.add(step_id)
        started = time.monotonic()
        try:
            receipt = await execute(action)
            observation = receipt.get("observation")
            if receipt.get("status") != "completed" or not isinstance(observation, dict):
                observation = {
                    "error": "browser action did not complete",
                    "status": receipt.get("status"),
                }
        except Exception as error:
            await self.emit(
                "browser.action_finished",
                {
                    **details,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "outcome": "failed",
                    "error": str(error)[:2000],
                },
            )
            raise
        if diagnostic_url(observation.get("url")) and observation.get("bounded_snapshot"):
            self.has_page_observation = True
        await self.emit(
            "browser.action_finished",
            {
                **details,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "outcome": "failed" if observation.get("error") else "completed",
                "url": diagnostic_url(observation.get("url")) or details["url"],
                "title": str(observation.get("title") or "")[:256],
                "summary": str(observation.get("bounded_snapshot") or "")[:2000],
                "error": str(observation.get("error") or "")[:2000],
                "observation_sequence": observation.get("sequence"),
            },
        )
        return observation
