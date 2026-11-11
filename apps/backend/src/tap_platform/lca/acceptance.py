"""Separate technical replay from proof of the original reviewed requirements."""

import json
from typing import Any

from .agent_projection import project_agent_actions
from .document_acceptance import current_field_proven
from .source_acceptance import SourceAcceptance, ReviewedAcceptanceContract


def source_evidence(
    browser: Any, project: str, source: dict[str, str]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    execution = browser.store.get("execution", project, source["execution_id"])
    if (
        not execution
        or execution["status"] != "passed"
        or execution["request"].get("mode") != "live"
    ):
        raise ValueError("Source execution unavailable")
    if any(
        execution[key] != source[key]
        for key in ("task_id", "bundle_id", "input_sha256")
    ):
        raise ValueError("Source provenance mismatch")
    bundle = browser.store.get("bundle", project, source["bundle_id"])
    session = browser.store.get(
        "session", project, execution["request"].get("session_id")
    )
    if (
        not bundle
        or not session
        or bundle["task_id"] != source["task_id"]
        or session["task_id"] != source["task_id"]
        or bundle["input_sha256"] != source["input_sha256"]
    ):
        raise ValueError("Source provenance mismatch")
    inputs = json.loads(
        browser.objects.read(
            bundle["input_ref"], bundle["input_sha256"], max_bytes=1_000_000
        )
    )
    if not isinstance(inputs, dict):
        raise ValueError("Invalid frozen input")
    contract = inputs.get("acceptance_contract")
    if "acceptance_contract" in inputs:
        ReviewedAcceptanceContract.model_validate(contract)
    metadata = SourceAcceptance.model_validate(
        {
            "source": source,
            "input_sha256": source["input_sha256"],
            "review_status": "reviewed"
            if contract is not None
            else "legacy_unreviewed",
            "acceptance_contract": contract,
        }
    ).model_dump(exclude_unset=True)
    rows: list[dict[str, Any]] = []
    after = 0
    while True:
        page = browser.store.list_actions(
            project, session["id"], after=after, limit=200
        )
        rows.extend(page)
        if len(rows) > 1000:
            raise ValueError("Source history too large")
        if len(page) < 200:
            break
        after = page[-1]["sequence"]
    evidence = {(item["kind"], item["sha256"]) for item in execution["evidence"] or []}
    finishes = [
        row
        for row in rows
        if row["request"].get("type") == "finish"
        and row["status"] == "completed"
        and (row.get("observation") or {}).get("result") == execution["result"]
        and evidence
        == {
            (item["kind"], item["sha256"])
            for item in (row.get("observation") or {}).get("evidence", [])
        }
    ]
    if len(finishes) != 1 or not evidence:
        raise ValueError("Source completion evidence unavailable")
    rows = [row for row in rows if row["sequence"] <= finishes[0]["sequence"]]
    if [row["sequence"] for row in rows] != list(range(1, finishes[0]["sequence"] + 1)):
        raise ValueError("Source history incomplete")
    return metadata, rows, bundle["allowed_origins"]


def validate_source_metadata(
    browser: Any, project: str, metadata: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    checked = SourceAcceptance.model_validate(metadata).model_dump(exclude_unset=True)
    actual, rows, origins = source_evidence(browser, project, checked["source"])
    if actual != checked:
        raise ValueError("Source acceptance evidence cannot be changed")
    return rows, origins


def _matches(field: dict[str, Any], row: dict[str, Any]) -> bool:
    request, observation = row["request"], row.get("observation") or {}
    extracted = observation.get("extracted") or {}
    labels = request.get("target_texts")
    documents = observation.get("documents")
    document_matches = (
        [
            entry
            for entry in documents or []
            if isinstance(entry, dict)
            and entry.get("frame_id") == extracted.get("source_frame_id")
            and entry.get("document_id") == extracted.get("source_document_id")
            and entry.get("url") == extracted.get("source_url")
        ]
        if isinstance(documents, list)
        else []
    )
    return bool(
        row["status"] == "completed"
        and not observation.get("error")
        and request.get("type") == "read"
        and request.get("name") == field["id"]
        and request.get("equals") == field["readback_value"]
        and request.get("attribute", "text") == field["attribute"]
        and isinstance(labels, list)
        and labels
        and all(label in field["target_texts"] for label in labels)
        and request.get("scope_text") == field.get("scope_text")
        and extracted.get("name") == field["id"]
        and extracted.get("value") == field["readback_value"]
        and type(observation.get("sequence")) is int
        and type(extracted.get("observation_sequence")) is int
        and extracted.get("observation_sequence") == observation["sequence"]
        and all(
            isinstance(extracted.get(key), str) and extracted[key]
            for key in ("source_frame_id", "source_document_id", "source_url")
        )
        and len(document_matches) == 1
    )


def _target_signature(target: dict[str, Any] | None) -> dict[str, Any] | None:
    # A user's explicit locator-review checkbox is not an executable change.
    return (
        {key: value for key, value in target.items() if key != "review_required"}
        if target
        else None
    )


def _execution_skeleton(
    definition: dict[str, Any], required_ids: set[str]
) -> list[dict[str, Any]]:
    targets = {target["id"]: target for target in definition["targets"]}
    read_only = {
        "assert_read",
        "assert_text",
        "assert_text_exact",
        "assert_url",
        "assert_value",
        "assert_checked",
        "assert_visible",
        "wait_for",
    }
    result = []
    for step in definition["steps"]:
        if step["operation"] in read_only and step["id"] not in required_ids:
            continue
        target = targets.get(step.get("target_id"))
        result.append(
            {
                "required_read_id": step["id"] if step["id"] in required_ids else None,
                "operation": step["operation"],
                "arguments": step["arguments"],
                "target": _target_signature(target),
                "frame_target": _target_signature(
                    targets.get(target.get("frame_target_id"))
                )
                if target
                else None,
                "target_contract": step.get("target_contract"),
            }
        )
    return result


def evaluate_contract(
    browser: Any,
    project: str,
    definition: dict[str, Any],
    execution: dict[str, Any] | None,
    canonical_results: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    metadata = definition.get("source_acceptance")
    if not metadata:
        return {
            "status": "legacy_unreviewed",
            "fields": [],
            "reason": "No reviewed source requirements",
        }
    try:
        rows, origins = validate_source_metadata(browser, project, metadata)
    except (ValueError, OSError, KeyError, TypeError):
        return {
            "status": "not_proven",
            "fields": [],
            "reason": "Original acceptance evidence unavailable or changed",
        }
    contract = metadata["acceptance_contract"]
    if contract is None:
        return {
            "status": "legacy_unreviewed",
            "fields": [],
            "reason": "Original AA task had no reviewed requirements",
        }
    steps = {step["id"]: step for step in definition["steps"]}
    targets = {target["id"]: target for target in definition["targets"]}
    results = {step["step_id"]: step for step in canonical_results or []}
    fields = []
    required_rows = {
        field["id"]: [row for row in rows if _matches(field, row)]
        for field in contract["fields"]
    }
    source_projection = project_agent_actions(
        rows,
        origins,
        [{"id": "verify", "keyword": "Then", "title": "Original verified actions"}],
        {row["id"]: "verify" for row in rows},
        metadata["source"],
        preserve_observations=True,
    )
    source_definition = source_projection["definition"]
    required_source_ids = {
        candidates[-1]["id"] for candidates in required_rows.values() if candidates
    }
    required_ids = {
        step["id"]
        for step in (source_definition or {}).get("steps", [])
        if step.get("provenance", {}).get("source_action_id") in required_source_ids
    }
    sequence_matches = source_definition is not None and _execution_skeleton(
        definition, required_ids
    ) == _execution_skeleton(source_definition, required_ids)
    for field in contract["fields"]:
        candidates = required_rows[field["id"]]
        reason = "Original field readback binding unavailable"
        expected = None
        if candidates:
            row = candidates[-1]
            projection = project_agent_actions(
                [row],
                origins,
                [
                    {
                        "id": "verify",
                        "keyword": "Then",
                        "title": "Verify original requirement",
                    }
                ],
                {row["id"]: "verify"},
                metadata["source"],
            )
            expected = projection["definition"]
        if expected:
            original = expected["steps"][0]
            current = steps.get(original["id"])
            current_result = results.get(original["id"])
            if (
                not current
                or any(
                    current.get(key) != original.get(key)
                    for key in (
                        "operation",
                        "arguments",
                        "target_id",
                        "target_contract",
                    )
                )
                or _target_signature(targets.get(original["target_id"]))
                != _target_signature(expected["targets"][0])
            ):
                reason = "Required readback action or target binding changed or missing"
            elif not sequence_matches:
                reason = "Write actions or required readback order changed; original requirements need renewed review"
            elif execution is None or execution.get("status") != "passed":
                reason = "Current execution is not passed"
            elif not current_result or current_result.get("status") != "passed":
                reason = "Current canonical readback evidence unavailable"
            elif current_result.get("output") != {
                "name": field["id"],
                "value": field["readback_value"],
            }:
                reason = "Current observed value does not prove the requirement"
            elif not current_field_proven(execution, field, original["id"]):
                reason = "Current document proof missing, pending, or stale"
            else:
                reason = ""
        fields.append(
            {
                "id": field["id"],
                "status": "passed" if not reason else "not_proven",
                "reason": reason,
            }
        )
    return {
        "status": "passed"
        if all(field["status"] == "passed" for field in fields)
        else "not_proven",
        "fields": fields,
        "reason": "",
    }
