#!/usr/bin/env python3
"""Produce and evaluate `intent-golden-observations-v1` observations for the
`AnswerPlan.agent_intent` six-value golden set (Task 11), in one script --
unlike `run-answer-golden.py`/`evaluate-answer-golden.py`'s run+evaluate
split, this gate has no real-mode baseline comparison, so run and evaluate
collapse into a single `--mode fake|real` CLI.

Fix round 1 (I-1): both modes now drive the production planning assembly
`AnswerPlanner(ModelGatewayPlanner(gateway, ...))` -- the exact construction
`tap.entrypoints.tapper_runtime._answer_planner` wires up -- through its
public `.plan()` entry point, with a nonempty `source_ids` (so the
`"clarify"`-because-no-sources heuristic branch never masks a real route).
This is the path production actually takes: `AnswerPlanner.plan()` only
*attempts* a model call when its own pre-model heuristics already decided
the route is `retrieve`/`graph` and the question is not ambiguous; it falls
back to a heuristic `AnswerPlan` (never raising) on a model timeout, invalid
schema or any other model-call exception. The gate scores the final,
validated `AnswerPlan.agent_intent` -- not a raw, unvalidated planner
response -- so a regression that breaks `agent_intent` propagation inside
`plan_answer.py`'s `build(...)` (Task 4) would show up here too (see
`tests/unit/quality/test_intent_golden.py::
test_fake_mode_surfaces_the_planners_agent_intent_not_the_heuristic_default`).

`plannerSource` is never asserted; it is *observed* via a spy wrapping the
`PlannerCall` (`_CallTracker`), then classified from what actually happened:

- `"model"`: the model was called and its plan was accepted
  (`plan.degradation_reason is None`).
- `"heuristic"`: `plan()` never attempted a model call at all (the
  pre-model heuristic route was not `retrieve`/`graph`, or the question was
  ambiguous) -- a legitimate, by-design path, not a failure.
- `"fallback"`: the model was called but its response was rejected or timed
  out (`plan.degradation_reason in {"invalid-plan", "planner-timeout",
  "planner-unavailable"}`) -- `AnswerPlanner.plan()` never raises for this,
  it falls back to a heuristic plan the same way production does for a real
  turn whose model call failed.

Fix round 2: `plannerSource` alone cannot tell a legitimately heuristic
case apart from a regression where `plan()` silently stopped calling the
model for a model-eligible question -- both would show up as `"heuristic"`.
Each observation also carries `modelEligible` (`_model_eligible`): whether
the question's *pre-model* heuristic route is `retrieve`/`graph`, derived
by calling the real `AnswerPlanner.plan()` with `model=None` (its own
model-call gate -- `if route not in {"retrieve", "graph"} or self._model is
None or ambiguous: return build()` -- always short-circuits the same way
when `self._model is None`, so this is the production heuristic itself,
never a re-implementation of its regexes). `fake_gate_passed` requires
`plannerSource == "model"` for every `modelEligible` case.

- `fake` (default CI path, no external services, no running model):
  `ModelGatewayPlanner` backed by `tap.testing.deterministic_model_gateway.
  DeterministicModelGateway`, which never raises on the `AnswerPlan` schema,
  so every eligible case is expected to come back `"model"`, never
  `"fallback"`. This only checks wiring -- it says nothing about
  classification accuracy, which depends on the particular model behind the
  gateway; see `tap.quality.intent_golden.fake_gate_passed`.
- `real` (opt-in, requires `TAP_RUN_QUALITY_INTENTS=1` and
  `TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:*`, mirroring
  `quality-answers-real`'s gating): `ModelGatewayPlanner` backed by the
  production `LiteLLMModelGateway`, built from `TapperSettings` the same way
  `tap.entrypoints.tapper_runtime` assembles it. A single case's model/schema
  error surfaces as `plan.degradation_reason` (`AnswerPlanner.plan()` already
  catches it, exactly as a real turn would) and is recorded `"fallback"`,
  never aborting the run; only a config/IO error (building the gateway,
  reading the golden file) aborts with exit code 2. Scored against Decision
  E's thresholds (`tap.quality.intent_golden.real_gate_passed`): overall
  accuracy >= 0.8 and every per-intent recall >= 0.6. Never run by an
  automated agent -- a human runs this against a real model.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.plan_answer import (
    AnswerPlan,
    AnswerPlanner,
    PlanningInput,
)
from tap.quality.intent_golden import (
    OBSERVATIONS_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    GoldenIntentCase,
    IntentGoldenSet,
    compute_intent_metrics,
    fake_gate_passed,
    observations_by_id,
    real_gate_passed,
    validate_intent_golden,
)

# Any nonempty, fixture-scoped source id works: `AnswerPlanner.plan()` only
# checks that a question has *some* authorized source before it will
# consider a `retrieve`/`graph` route (`not value.source_ids` otherwise
# forces `route="clarify"`); nothing in this gate ever resolves these ids
# against real knowledge sources.
_SOURCE_IDS: tuple[str, ...] = ("intent-golden-source-1",)

# `AnswerPlan.degradation_reason` values that mean the model was attempted
# and its response was rejected or unreachable -- never raised by
# `AnswerPlanner.plan()`, which falls back to a heuristic plan instead. Any
# other `degradation_reason` (e.g. `"capability-unavailable"`) means the
# model was never eligible in the first place -- that is `"heuristic"`, not
# `"fallback"`.
_FALLBACK_REASONS = frozenset(
    {"invalid-plan", "planner-timeout", "planner-unavailable"}
)


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


async def _redact(text: str) -> str:
    return text


def _planning_input(
    case: GoldenIntentCase, *, source_ids: tuple[str, ...]
) -> PlanningInput:
    return PlanningInput(
        project_id=VALIDATION_SCOPE.project_id,
        turn_id=f"intent-golden-{case.id}",
        input_digest=_digest(f"input-{case.id}"),
        acl_digest=_digest("acl"),
        policy_digest=_digest("policy"),
        original_question=case.question,
        model_alias="qwen-plus",
        source_ids=source_ids,
    )


PlannerCall = Callable[[PlanningInput, float], Awaitable[dict[str, Any]]]


class _CallTracker:
    """Spies on a `PlannerCall` to observe, not assume, whether
    `AnswerPlanner.plan()` actually invoked the model for a given case."""

    def __init__(self, inner: PlannerCall) -> None:
        self._inner = inner
        self.called = False

    async def __call__(self, value: PlanningInput, timeout: float) -> dict[str, Any]:
        self.called = True
        return await self._inner(value, timeout)


def _planner_source(plan: AnswerPlan, *, model_called: bool) -> str:
    if plan.degradation_reason in _FALLBACK_REASONS:
        return "fallback"
    return "model" if model_called else "heuristic"


async def _heuristic_route(
    case: GoldenIntentCase, *, source_ids: tuple[str, ...] = _SOURCE_IDS
) -> str:
    """The question's pre-model heuristic route, derived from the real
    `AnswerPlanner.plan()` -- not a re-implementation of its routing
    regexes. `AnswerPlanner.plan()`'s own model-call gate is `if route not
    in {"retrieve", "graph"} or self._model is None or ambiguous: return
    build()` -- when `self._model is None` that condition is always true
    regardless of route, so `AnswerPlanner(None).plan(value)` always takes
    this short-circuit and returns exactly the same pre-model heuristic
    plan a model-backed call would have started from, computed by the
    identical production code. Fix round 2 (gap): this is what lets
    `fake_gate_passed` require `plannerSource == "model"` specifically for
    cases whose heuristic route is `retrieve`/`graph`, instead of trusting
    that every `"heuristic"` observation was legitimately heuristic."""
    plan = await AnswerPlanner(None).plan(_planning_input(case, source_ids=source_ids))
    return plan.route


async def _model_eligible(
    case: GoldenIntentCase, *, source_ids: tuple[str, ...] = _SOURCE_IDS
) -> bool:
    """Whether `AnswerPlanner.plan()` would even attempt a model call for
    `case` -- i.e. whether its pre-model heuristic route is `retrieve` or
    `graph` (see `_heuristic_route`)."""
    route = await _heuristic_route(case, source_ids=source_ids)
    return route in ("retrieve", "graph")


async def _plan_case(
    case: GoldenIntentCase,
    model: PlannerCall,
    *,
    source_ids: tuple[str, ...] = _SOURCE_IDS,
) -> tuple[AnswerPlan, bool]:
    """Drive one golden case through the production `AnswerPlanner(ModelGatewayPlanner(
    ...))` assembly's `.plan()` entry point, returning the final validated
    `AnswerPlan` and whether the model was actually called (observed via
    `_CallTracker`, not assumed from the case's category)."""
    tracker = _CallTracker(model)
    plan = await AnswerPlanner(tracker).plan(
        _planning_input(case, source_ids=source_ids)
    )
    return plan, tracker.called


async def _classify_cases(
    golden: IntentGoldenSet, model: PlannerCall
) -> list[dict[str, Any]]:
    """Score every golden case through the real planning path and record the
    final `AnswerPlan.agent_intent`, the observed `plannerSource`, and
    whether the case was `modelEligible` per the production planner's own
    pre-model heuristic (`_model_eligible`) -- so `fake_gate_passed` can
    require `plannerSource == "model"` specifically for eligible cases,
    not just "no fallback" (fix round 2)."""
    observations: list[dict[str, Any]] = []
    for case in golden.cases:
        model_eligible = await _model_eligible(case)
        plan, model_called = await _plan_case(case, model)
        observations.append(
            {
                "id": case.id,
                "agentIntent": plan.agent_intent,
                "plannerSource": _planner_source(plan, model_called=model_called),
                "degradationReason": plan.degradation_reason,
                "modelEligible": model_eligible,
            }
        )
    return observations


async def run_fake(golden: IntentGoldenSet) -> dict[str, Any]:
    """Drive the production `AnswerPlanner(ModelGatewayPlanner(...))` assembly
    with `DeterministicModelGateway` standing in for the production
    `LiteLLMModelGateway` -- the same construction
    `tap.entrypoints.tapper_runtime._answer_planner` wires up."""
    from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
    from tap.modules.ai.adapters.litellm_catalog import ModelRoles
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner
    from tap.testing.deterministic_model_gateway import DeterministicModelGateway

    started_at = _now_iso()
    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="private-fake-key",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    gateway = DeterministicModelGateway(config, scope=VALIDATION_SCOPE, redact=_redact)
    planner = ModelGatewayPlanner(gateway, scope=VALIDATION_SCOPE, redact=_redact)

    observations = await _classify_cases(golden, planner)

    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "mode": "fake",
        "observations": observations,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
    }


async def run_real(golden: IntentGoldenSet) -> dict[str, Any]:
    """Drive the production `AnswerPlanner(ModelGatewayPlanner(...))` assembly
    with the production `LiteLLMModelGateway`, built from `TapperSettings`
    the same way `tap.entrypoints.tapper_runtime` assembles it for the chat
    graph. Opt-in only (see module docstring); never invoked by an automated
    agent. A single case's model/schema error is caught inside
    `AnswerPlanner.plan()` itself (exactly as a real turn's would be) and
    surfaces as `plan.degradation_reason` -> `"fallback"`, never aborting
    the loop; only a config/IO error (building the gateway, reading the
    golden file) aborts the whole run."""
    from tap.entrypoints.tapper_runtime import TapperSettings
    from tap.modules.ai.adapters.litellm import (
        LiteLLMModelGateway,
        LiteLLMModelGatewayConfig,
    )
    from tap.modules.ai.adapters.litellm_catalog import LiteLLMCatalog, ModelRoles
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner

    started_at = _now_iso()
    settings = TapperSettings.from_mapping(dict(os.environ))
    config = LiteLLMModelGatewayConfig(
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
        roles=ModelRoles(
            settings.default_chat_model, settings.embedding_model, settings.vision_model
        ),
        embedding_dimension=settings.embedding_dimension,
        timeout_seconds=settings.model_timeout_seconds,
    )
    catalog = LiteLLMCatalog(
        base_url=settings.litellm_base_url, api_key=settings.litellm_api_key
    )
    gateway = LiteLLMModelGateway(
        config, scope=VALIDATION_SCOPE, redact=_redact, catalog=catalog
    )
    try:
        planner = ModelGatewayPlanner(gateway, scope=VALIDATION_SCOPE, redact=_redact)
        observations = await _classify_cases(golden, planner)
    finally:
        await gateway.aclose()

    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "mode": "real",
        "observations": observations,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
    }


def evaluate(
    golden: IntentGoldenSet, observations_document: dict[str, Any]
) -> dict[str, Any]:
    """Score `observations_document` against `golden` and build the report;
    pure, no I/O."""
    by_id = observations_by_id(observations_document["observations"])
    metrics = compute_intent_metrics(golden, by_id)
    mode = observations_document["mode"]

    planner_source_counts: dict[str, int] = {}
    for observation in by_id.values():
        source = observation.get("plannerSource")
        if isinstance(source, str):
            planner_source_counts[source] = planner_source_counts.get(source, 0) + 1

    report: dict[str, Any] = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "mode": mode,
        "metrics": metrics,
        "plannerSourceCounts": planner_source_counts,
    }
    if mode == "fake":
        passed = fake_gate_passed(golden, by_id)
        report["fakeGatePassed"] = passed
    else:
        passed = real_gate_passed(metrics)
        report["realGatePassed"] = passed
    report["passed"] = passed
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "real"), required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if arguments.mode == "real":
        if os.environ.get("TAP_RUN_QUALITY_INTENTS") != "1":
            print(
                "error: --mode real requires TAP_RUN_QUALITY_INTENTS=1", file=sys.stderr
            )
            return 2
        authorization = os.environ.get("TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION", "")
        if not authorization.startswith("approved:") or authorization == "approved:":
            print(
                "error: --mode real requires explicit model execution authorization "
                "(TAP_QUALITY_MODEL_EXECUTION_AUTHORIZATION=approved:<reviewer>)",
                file=sys.stderr,
            )
            return 2

    try:
        golden_json = json.loads(arguments.golden.read_text(encoding="utf-8"))
        golden = validate_intent_golden(golden_json)
        observations_document = asyncio.run(
            run_fake(golden) if arguments.mode == "fake" else run_real(golden)
        )
        report = evaluate(golden, observations_document)
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
