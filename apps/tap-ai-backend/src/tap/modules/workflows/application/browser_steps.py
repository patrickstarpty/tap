"""Persist a business plan before inspection and correlate actual browser actions."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from tap.modules.workflows.application.browser_documents import document_map, same_document
from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract

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
    def __init__(self, emit: Emit, acceptance: BrowserAcceptanceContract | None = None) -> None:
        self.emit = emit
        self.acceptance = acceptance
        self.required_fields = {f.id: f for f in acceptance.fields} if acceptance else {}
        self.required_reads: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.documents: dict[str, dict[str, str]] = {}
        self.pending_proofs: dict[str, dict[str, Any]] = {}
        self.last_sequence = 0
        self.session_id: str | None = None
        self.recorded_actions: list[dict[str, Any]] = []
        self.steps: dict[str, dict[str, str]] = {}
        self.used: set[str] = set()
        self.revision = 0
        self.has_page_observation = False
        self.live_checks: dict[str, dict[str, Any]] = {}
        self.input_proofs: dict[str, dict[str, Any]] = {}
        self.input_checks: dict[str, dict[str, Any]] = {}
        self.current_url_sha256 = ""
        self.unknown_action = False

    def bind_session(self, session: dict[str, Any], project_id: str, task_id: str) -> None:
        if self.acceptance is None:
            return
        if (
            session.get("project_id") != project_id
            or session.get("task_id") != task_id
            or not isinstance(session.get("session_id"), str)
            or not session["session_id"]
            or session.get("status") != "active"
        ):
            raise ValueError("reviewed input browser session provenance mismatch")
        self.session_id = session["session_id"]

    @property
    def required_inputs_complete(self) -> bool:
        verified = {
            action.get("name")
            for ident, action in self.input_checks.items()
            if ident in self.input_proofs
            and (field := self.required_fields.get(str(action.get("name", "")))) is not None
            and field.matches_read(action)
            and self.input_proofs[ident].get("observed_value") == field.readback_value
            and type(self.input_proofs[ident].get("observation_sequence")) is int
        }
        return not self.unknown_action and self.required_fields.keys() <= verified

    def verify_finished_result(self, result: dict[str, Any], observation: dict[str, Any]) -> None:
        if self.acceptance is None:
            return
        if self.session_id is not None and observation.get("session_id") != self.session_id:
            raise ValueError("reviewed input finish session provenance mismatch")
        self._advance_document_proofs(
            {"type": "finish"},
            observation,
            type(observation.get("sequence")) is int
            and observation["sequence"] > self.last_sequence,
        )
        outputs = result.get("outputs")
        if not self.required_inputs_complete or not isinstance(outputs, list) or len(outputs) != 1:
            raise ValueError("reviewed input finish evidence missing")
        output = outputs[0]
        if not isinstance(output, dict) or not isinstance(output.get("provenance"), dict):
            raise ValueError("reviewed input finish provenance missing")
        for name, field in self.required_fields.items():
            proof = next(
                (
                    self.input_proofs[ident]
                    for ident, check in self.input_checks.items()
                    if check.get("name") == name and ident in self.input_proofs
                ),
                None,
            )
            source = output["provenance"].get(name)
            if (
                proof is None
                or not isinstance(source, dict)
                or output.get(name) != field.readback_value
                or type(source.get("observation_sequence")) is not int
                or source.get("observation_sequence") != proof["observation_sequence"]
                or source.get("source_frame_id") != proof.get("source_frame_id")
                or source.get("source_document_id") != proof.get("source_document_id")
                or not isinstance(source.get("source_url"), str)
                or hashlib.sha256(source["source_url"].encode()).hexdigest()
                != proof["source_url_sha256"]
            ):
                raise ValueError("reviewed input finish evidence changed: " + name)

    async def refresh_required_inputs(self, execute: Execute, prefix: str) -> list[dict[str, Any]]:
        """Read known current-page fields again before leaving or finishing. No writes."""
        records = []
        for name, (action, source) in list(self.required_reads.items()):
            if not same_document(source, self.documents):
                continue
            result = await self.handle(action, f"{prefix}-{name}", execute)
            proof = self.input_proofs.get(action["step"])
            if result.get("error") or self.unknown_action or proof is None:
                raise ValueError("reviewed input readback unavailable or changed: " + name)
            records.append({**action, "action_id": result["action_id"], "url": result.get("url")})
        return records

    @property
    def live_complete(self) -> bool:
        results = {
            ident
            for ident, step in self.steps.items()
            if step["keyword"] == "Then"
            and step.get("verification_kind", "result") == "result"
            and self.input_checks.get(ident, {}).get("name") not in self.required_fields
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
            and self.required_inputs_complete
        )

    @property
    def final_checks(self) -> list[tuple[dict[str, Any], bool]]:
        current_inputs = [
            (self.input_checks[ident], True)
            for ident, proof in self.input_proofs.items()
            if (
                same_document(proof, self.documents)
                if self.acceptance is not None
                else proof["source_url_sha256"] == self.current_url_sha256
            )
            and ident in self.input_checks
            and self.input_checks[ident].get("name") not in self.required_fields
        ]
        return current_inputs + [(action, False) for action in self.live_checks.values()]

    def _advance_document_proofs(
        self, action: dict[str, Any], observation: dict[str, Any], completed: bool
    ) -> None:
        """Only actual same-frame document transitions can archive pre-click evidence.

        A click with a still-live document creates pending evidence, not a pass.
        Read-only observations may resolve delayed navigation. No later write can.
        """
        after = document_map(observation)
        before = self.documents
        self.documents = after
        writes = action.get("type") in {"navigate", "click", "fill", "select", "press", "scroll"}
        if not completed or not after:
            self.input_proofs.clear()
            self.pending_proofs.clear()
            self.live_checks.clear()
        elif writes:
            self.pending_proofs.clear()
            for ident, proof in list(self.input_proofs.items()):
                self.input_proofs.pop(ident)
                frame = after.get(proof.get("source_frame_id", ""))
                if not frame or not same_document(proof, before):
                    continue  # Missing frame and previously archived evidence are not navigation.
                if action.get("type") in {"navigate", "click"} and not same_document(proof, after):
                    self.input_proofs[ident] = {
                        **proof,
                        "departure_document_id": frame["document_id"],
                    }
                elif action.get("type") == "click":
                    self.pending_proofs[ident] = proof
        else:
            if before != after:
                self.live_checks.clear()
            for ident, proof in list(self.input_proofs.items()):
                frame = after.get(proof.get("source_frame_id", ""))
                expected = proof.get("departure_document_id", proof.get("source_document_id"))
                if not frame or frame["document_id"] != expected:
                    self.input_proofs.pop(ident)
            for ident, proof in list(self.pending_proofs.items()):
                frame = after.get(proof.get("source_frame_id", ""))
                if (
                    action.get("type") not in {"snapshot", "wait", "read", "screenshot"}
                    or not frame
                    or not same_document(proof, before)
                ):
                    self.pending_proofs.pop(ident)
                elif not same_document(proof, after):
                    if not proof.get("post_click_checked"):
                        self.pending_proofs.pop(ident)
                        continue
                    self.input_proofs[ident] = {
                        **proof,
                        "departure_document_id": frame["document_id"],
                    }
                    self.pending_proofs.pop(ident)
        self.input_checks = {
            ident: check
            for ident, check in self.input_checks.items()
            if ident in self.input_proofs or ident in self.pending_proofs
        }

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
        pending_read = self.pending_proofs.get(step_id) if action.get("type") == "read" else None
        writes = {"navigate", "click", "fill", "select", "press", "scroll"}
        previous_url_sha256 = self.current_url_sha256
        if action.get("type") in writes:
            self.live_checks.clear()
        if action.get("type") == "read":
            self.live_checks.pop(step_id, None)
            self.input_proofs.pop(step_id, None)
            self.pending_proofs.pop(step_id, None)
            self.input_checks.pop(step_id, None)
            read_name = action.get("name")
            if isinstance(read_name, str):
                for ident, check in list(self.input_checks.items()):
                    if check.get("name") == read_name:
                        self.input_checks.pop(ident)
                        self.input_proofs.pop(ident, None)
                        self.pending_proofs.pop(ident, None)
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
            if receipt.get("status") not in {"completed", "failed"} or not isinstance(
                observation, dict
            ):
                observation = {
                    "error": "browser action did not complete",
                    "status": receipt.get("status"),
                }
            elif receipt.get("status") == "failed" and not observation.get("error"):
                observation = {**observation, "error": "browser action did not complete"}
        except Exception as error:
            if action.get("type") in writes:
                self.input_proofs.clear()
                self.pending_proofs.clear()
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
        if self.session_id is not None and (
            receipt.get("session_id") != self.session_id
            or observation.get("session_id") != self.session_id
        ):
            self.unknown_action = True
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
        completed = (
            receipt.get("status") == "completed"
            and not observation.get("error")
            and not self.unknown_action
        )
        sequence = observation.get("sequence")
        fresh_sequence = type(sequence) is int and sequence > self.last_sequence
        if type(sequence) is int:
            self.last_sequence = max(sequence, self.last_sequence)
        if self.unknown_action:
            self.live_checks.clear()
            self.input_proofs.clear()
            self.pending_proofs.clear()
            self.input_checks.clear()
        elif self.acceptance is not None:
            self._advance_document_proofs(action, observation, completed and fresh_sequence)
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
            if self.acceptance is not None:
                # A passive/unexplained page change cannot prove the input was submitted.
                self.input_proofs.clear()
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
            required = self.required_fields.get(str(action.get("name", "")))
            if step.get("verification_kind") == "input" or required is not None:
                receipt_id = receipt.get("action_id")
                if (
                    isinstance(action.get("equals"), str)
                    and extracted.get("value") == action["equals"]
                    and extracted.get("name") == action.get("name")
                    and source_url
                    and isinstance(receipt_id, str)
                    and receipt_id
                    and (required is None or required.matches_read(action))
                    and (
                        self.acceptance is None
                        or (
                            fresh_sequence
                            and type(extracted.get("observation_sequence")) is int
                            and extracted.get("observation_sequence") == sequence
                            and same_document(extracted, self.documents)
                            and source_raw_url
                            == self.documents[extracted["source_frame_id"]]["url"]
                        )
                    )
                ):
                    self.input_proofs[step_id] = {
                        "source_url": source_url,
                        "source_url_sha256": source_url_sha256,
                        "receipt_id": receipt_id,
                        "observed_value": extracted["value"],
                        **(
                            {
                                "observation_sequence": sequence,
                                "source_frame_id": extracted["source_frame_id"],
                                "source_document_id": extracted["source_document_id"],
                            }
                            if self.acceptance is not None
                            else {}
                        ),
                    }
                    self.input_checks[step_id] = action
                    if pending_read and (
                        pending_read.get("source_frame_id") == extracted.get("source_frame_id")
                        and pending_read.get("source_document_id")
                        == extracted.get("source_document_id")
                    ):
                        # Fresh evidence can also resolve this click's delayed navigation.
                        self.pending_proofs[step_id] = {
                            **self.input_proofs[step_id],
                            "post_click_checked": True,
                        }
                    if required is not None:
                        self.required_reads[required.id] = (
                            {**action, "step": step_id},
                            self.input_proofs[step_id].copy(),
                        )
            elif (
                same_document(extracted, self.documents)
                and source_raw_url == self.documents[extracted["source_frame_id"]]["url"]
                if self.acceptance is not None
                else not source_url_sha256 or source_url_sha256 == observed_url_sha256
            ):
                self.live_checks[step_id] = action
        if completed:
            self.recorded_actions.append(
                {
                    **action,
                    "step": step_id,
                    "action_id": receipt.get("action_id", action_id),
                    "url": observation.get("url"),
                }
            )
        if completed and action.get("type") == "click" and self.acceptance is not None:
            for ident in list(self.pending_proofs):
                name = str(self.input_checks.get(ident, {}).get("name", ""))
                source_read = self.required_reads.get(name)
                if source_read is None:
                    continue
                reread = await self.handle(source_read[0], f"{action_id}-verify-{name}", execute)
                if reread.get("error") or ident not in self.input_proofs:
                    raise ValueError("reviewed input changed or unavailable after click: " + name)
        return {**observation, "action_id": receipt.get("action_id", action_id)}
