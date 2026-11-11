"""Persist a business plan before inspection and correlate actual browser actions."""

from __future__ import annotations

import hashlib
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
        self.live_checks: dict[str, dict[str, Any]] = {}
        self.input_proofs: dict[str, dict[str, str]] = {}
        self.input_checks: dict[str, dict[str, Any]] = {}
        self.current_url_sha256 = ""
        self.unknown_action = False

    @property
    def live_complete(self) -> bool:
        results = {
            ident
            for ident, step in self.steps.items()
            if step["keyword"] == "Then" and step.get("verification_kind", "result") == "result"
        }
        inputs = {
            ident
            for ident, step in self.steps.items()
            if step["keyword"] == "Then" and step.get("verification_kind") == "input"
        }
        return (
            bool(results)
            and results <= self.live_checks.keys()
            and inputs <= self.input_proofs.keys()
            and not self.unknown_action
        )

    @property
    def final_checks(self) -> list[tuple[dict[str, Any], bool]]:
        current_inputs = [
            (self.input_checks[ident], True)
            for ident, proof in self.input_proofs.items()
            if proof["source_url_sha256"] == self.current_url_sha256 and ident in self.input_checks
        ]
        return current_inputs + [(action, False) for action in self.live_checks.values()]

    async def handle(
        self, payload: dict[str, Any], action_id: str, execute: Execute
    ) -> dict[str, Any]:
        if payload.get("type") == "plan":
            raw = payload.get("steps")
            if not isinstance(raw, list) or not 1 <= len(raw) <= 24:
                return {"error": "BDD plan requires 1 to 24 steps"}
            steps = {}
            for index, item in enumerate(raw, start=1):
                if not isinstance(item, dict) or set(item) - {
                    "id",
                    "keyword",
                    "title",
                    "expected_result",
                    "verification_kind",
                }:
                    return {"error": "invalid BDD plan step"}
                ident, keyword, title = item.get("id"), item.get("keyword"), item.get("title")
                expected = item.get("expected_result", "")
                if (
                    not isinstance(ident, str)
                    or not _ID.fullmatch(ident)
                    or ident in steps
                    or not isinstance(keyword, str)
                    or keyword not in {"Given", "When", "Then", "And"}
                    or not isinstance(title, str)
                    or not 1 <= len(title.strip()) <= 200
                    or not isinstance(expected, str)
                    or len(expected) > 400
                ):
                    return {"error": "invalid BDD plan step"}
                if "verification_kind" in item:
                    label = f"BDD plan step {index} (id {ident})"
                    if not isinstance(item["verification_kind"], str) or item[
                        "verification_kind"
                    ] not in {"input", "result"}:
                        return {"error": f"{label}: verification_kind must be 'input' or 'result'"}
                    if keyword != "Then":
                        return {
                            "error": f"{label}: verification_kind is only allowed on Then steps; "
                            "change keyword to Then or remove verification_kind"
                        }
                steps[ident] = {
                    "id": ident,
                    "keyword": keyword,
                    "title": title.strip(),
                    "expected_result": expected,
                    **(
                        {"verification_kind": item["verification_kind"]}
                        if "verification_kind" in item
                        else {}
                    ),
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
                {
                    "revision": self.revision + 1,
                    "steps": list(steps.values()),
                    "reason": reason,
                    **(
                        {"contract": payload["contract"], "plan_digest": payload["plan_digest"]}
                        if "contract" in payload and "plan_digest" in payload
                        else {}
                    ),
                },
            )
            self.steps = steps
            self.revision += 1
            return {"status": "planned", "revision": self.revision, "step_ids": list(steps)}
        if not self.steps:
            return {"error": "Create the BDD plan before browser actions"}
        step_id = payload.get("step")
        if not isinstance(step_id, str) or step_id not in self.steps:
            return {"error": "Browser action must reference a planned BDD step id"}
        if self.unknown_action:
            return {
                "error": "Previous browser action outcome is unknown; do not retry",
                "status": "unknown",
            }
        action = {key: value for key, value in payload.items() if key != "step"}
        writes = {"navigate", "click", "fill", "select", "press"}
        previous_url_sha256 = self.current_url_sha256
        if action.get("type") in writes:
            self.live_checks.clear()
        if action.get("type") == "read":
            self.live_checks.pop(step_id, None)
            self.input_proofs.pop(step_id, None)
            self.input_checks.pop(step_id, None)
            read_name = action.get("name")
            if isinstance(read_name, str):
                for ident, check in list(self.input_checks.items()):
                    if check.get("name") == read_name:
                        self.input_checks.pop(ident)
                        self.input_proofs.pop(ident, None)
                for ident, check in list(self.live_checks.items()):
                    if check.get("name") == read_name:
                        self.live_checks.pop(ident)
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
            self.unknown_action = receipt.get("status") == "unknown"
            if receipt.get("status") != "completed" or not isinstance(observation, dict):
                observation = {
                    "error": "browser action did not complete",
                    "status": receipt.get("status"),
                }
        except Exception as error:
            if action.get("type") in writes:
                self.input_proofs.clear()
                self.input_checks.clear()
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
        # A renderer/session loss cannot establish whether a write took effect.
        # Latch it before another browser or model operation can be admitted.
        error_head = str(observation.get("error") or "").split("\n", 1)[0]
        if error_head.rsplit(":", 1)[-1].strip().lower() in {
            "target crashed",
            "page crashed",
            "browser page crashed",
            "target page, context or browser has been closed",
        }:
            self.unknown_action = True
            self.live_checks.clear()
        observed_url = diagnostic_url(observation.get("url"))
        raw_url = observation.get("url")
        full_observed_url = isinstance(raw_url, str) and len(raw_url) < 2048
        observed_url_sha256 = (
            hashlib.sha256(raw_url.encode()).hexdigest()
            if observed_url and isinstance(raw_url, str)
            else ""
        )
        completed = receipt.get("status") == "completed" and not observation.get("error")
        if self.unknown_action:
            self.live_checks.clear()
            self.input_proofs.clear()
            self.input_checks.clear()
        elif action.get("type") in writes:
            if not completed or not observed_url:
                self.input_proofs.clear()
            elif (
                action.get("type") in {"navigate", "click"}
                and previous_url_sha256
                and observed_url_sha256 != previous_url_sha256
            ):
                self.input_proofs = {
                    ident: proof
                    for ident, proof in self.input_proofs.items()
                    if proof["source_url_sha256"] != observed_url_sha256
                }
            else:
                self.input_proofs = {
                    ident: proof
                    for ident, proof in self.input_proofs.items()
                    if proof["source_url_sha256"] not in {previous_url_sha256, observed_url_sha256}
                }
            self.input_checks = {
                ident: check
                for ident, check in self.input_checks.items()
                if ident in self.input_proofs
            }
        elif (
            observed_url_sha256
            and previous_url_sha256
            and observed_url_sha256 != previous_url_sha256
        ):
            self.live_checks.clear()
            self.input_proofs = {
                ident: proof
                for ident, proof in self.input_proofs.items()
                if proof["source_url_sha256"] != observed_url_sha256
            }
            self.input_checks = {
                ident: check
                for ident, check in self.input_checks.items()
                if ident in self.input_proofs
            }
        if completed and observed_url:
            self.current_url_sha256 = observed_url_sha256
        if observed_url and observation.get("bounded_snapshot"):
            self.has_page_observation = True
        await self.emit(
            "browser.action_finished",
            {
                **details,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "outcome": (
                    "unknown"
                    if self.unknown_action
                    else "failed"
                    if observation.get("error")
                    else "completed"
                ),
                "url": diagnostic_url(observation.get("url")) or details["url"],
                "title": str(observation.get("title") or "")[:256],
                "summary": str(observation.get("bounded_snapshot") or "")[:2000],
                "error": str(observation.get("error") or "")[:2000],
                "observation_sequence": observation.get("sequence"),
            },
        )
        checked = (
            action.get("equals") is not None
            or bool(action.get("contains"))
            or action.get("nonempty") is True
            or action.get("positive") is True
            or bool(action.get("pattern"))
        )
        if (
            action.get("type") == "read"
            and step["keyword"] == "Then"
            and completed
            and checked
            and isinstance(observation.get("extracted"), dict)
            and full_observed_url
        ):
            extracted = observation["extracted"]
            source_raw_url = extracted.get("source_url", raw_url)
            source_url = (
                diagnostic_url(source_raw_url)
                if isinstance(source_raw_url, str) and len(source_raw_url) < 2048
                else ""
            )
            source_url_sha256 = (
                hashlib.sha256(source_raw_url.encode()).hexdigest()
                if source_url and isinstance(source_raw_url, str)
                else ""
            )
            if step.get("verification_kind") == "input":
                receipt_id = receipt.get("action_id")
                if (
                    isinstance(action.get("equals"), str)
                    and extracted.get("value") == action["equals"]
                    and extracted.get("name") == action.get("name")
                    and source_url
                    and isinstance(receipt_id, str)
                    and receipt_id
                ):
                    self.input_proofs[step_id] = {
                        "source_url": source_url,
                        "source_url_sha256": source_url_sha256,
                        "receipt_id": receipt_id,
                        "observed_value": extracted["value"],
                    }
                    self.input_checks[step_id] = action
            elif not source_url_sha256 or source_url_sha256 == observed_url_sha256:
                self.live_checks[step_id] = action
        return {**observation, "action_id": receipt.get("action_id", action_id)}
