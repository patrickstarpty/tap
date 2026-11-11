"""Authorize a read-only LCA projection from a completed live AA execution."""

import json
from typing import Any

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.service import BrowserService, Forbidden, Invalid

from .agent_projection import project_agent_actions
from .definition import validate_definition
from .source_acceptance import SourceAcceptance, ReviewedAcceptanceContract


def export_agent_draft(
    browser: BrowserService,
    principal: AccessPrincipal,
    execution_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    # Read grants are distinct; a model/task cannot publish or run this projection.
    browser.get_execution(principal, execution_id)
    project = browser._project(principal, "browser.actions.read")
    execution = browser.store.get("execution", project, execution_id)
    if execution is None:
        raise Forbidden("AA execution unavailable")
    if set(body) != {"task_id", "bundle_id", "input_sha256", "plan", "action_steps"}:
        raise Invalid("Invalid AA projection request")
    if any(
        body.get(key) != execution[key]
        for key in ("task_id", "bundle_id", "input_sha256")
    ):
        raise Forbidden("AA source provenance mismatch")
    if execution["status"] != "passed" or execution["request"].get("mode") != "live":
        raise Invalid("Only completed live AA executions can be projected")
    session_id = execution["request"].get("session_id")
    session = browser.store.get("session", project, session_id)
    bundle = browser.store.get("bundle", project, execution["bundle_id"])
    if (
        session is None
        or bundle is None
        or session["task_id"] != execution["task_id"]
        or bundle["task_id"] != execution["task_id"]
        or bundle["input_sha256"] != execution["input_sha256"]
    ):
        raise Forbidden("AA session provenance mismatch")
    plan, action_steps = body["plan"], body["action_steps"]
    if (
        not isinstance(plan, list)
        or not 1 <= len(plan) <= 100
        or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str)
            for item in plan
        )
        or not isinstance(action_steps, dict)
        or len(action_steps) > 1000
        or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in action_steps.items()
        )
    ):
        raise Invalid("Invalid AA business step mapping")
    rows = []
    after = 0
    while True:
        page = browser.store.list_actions(project, session_id, after=after, limit=200)
        rows.extend(page)
        if len(rows) > 1000:
            raise Invalid("AA action history exceeds projection limit")
        if len(page) < 200:
            break
        after = page[-1]["sequence"]
    # Tie the cutoff to the stored final DOM result and evidence, never a caller's
    # claimed completion or the current session page.
    evidence = {(x["kind"], x["sha256"]) for x in execution["evidence"] or []}
    finishes = [
        row
        for row in rows
        if row["request"].get("type") == "finish"
        and row["status"] == "completed"
        and (row.get("observation") or {}).get("result") == execution["result"]
        and evidence
        == {
            (x["kind"], x["sha256"])
            for x in (row.get("observation") or {}).get("evidence", [])
        }
    ]
    if len(finishes) != 1 or not evidence:
        raise Invalid("AA completion evidence unavailable or ambiguous")
    rows = [row for row in rows if row["sequence"] <= finishes[0]["sequence"]]
    if [row["sequence"] for row in rows] != list(range(1, finishes[0]["sequence"] + 1)):
        raise Invalid("AA action history is incomplete")
    source = {key: execution[key] for key in ("task_id", "bundle_id", "input_sha256")}
    source["execution_id"] = execution_id
    result = project_agent_actions(
        rows, bundle["allowed_origins"], plan, action_steps, source
    )
    # Read only the content-addressed original bundle inputs. Never infer a
    # contract from the model's plan, current form or edited LCA definition.
    try:
        input_data = json.loads(
            browser.objects.read(
                bundle["input_ref"], bundle["input_sha256"], max_bytes=1_000_000
            )
        )
    except (ValueError, OSError, TypeError) as error:
        raise Invalid("Frozen AA inputs unavailable or invalid") from error
    if not isinstance(input_data, dict):
        raise Invalid("Frozen AA inputs unavailable or invalid")
    raw_contract = input_data.get("acceptance_contract")
    result["acceptance_contract"] = raw_contract
    result["contract_review_status"] = "legacy_unreviewed"
    try:
        if "acceptance_contract" in input_data:
            ReviewedAcceptanceContract.model_validate(raw_contract)
            result["contract_review_status"] = "reviewed"
        source_acceptance = SourceAcceptance.model_validate(
            {
                "source": source,
                "input_sha256": bundle["input_sha256"],
                "review_status": result["contract_review_status"],
                "acceptance_contract": raw_contract,
            }
        ).model_dump(exclude_unset=True)
    except ValueError:
        result["contract_review_status"] = "invalid"
        result["complete"] = False
        result["definition"] = None
        result["issues"].append(
            {
                "action_id": "",
                "sequence": 0,
                "operation": "",
                "reason": "invalid_source_acceptance_contract",
            }
        )
    else:
        if result["definition"] is not None:
            result["definition"] = validate_definition(
                {**result["definition"], "source_acceptance": source_acceptance}
            ).model_dump(exclude_unset=True)
    result["evidence"] = execution["evidence"]
    return result
