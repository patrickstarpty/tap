"""One graph-local planning call followed by deterministic authority admission."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from tap.modules.chat.domain.answer_plan import (
    AnswerPlan,
    PlannedQuery,
    PlanningInput,
    protected_constraints,
)
from tap.modules.chat.domain.conversations import TurnInputSnapshot
from tap.modules.knowledge.api import AuthorizedAnswerExecution as _AuthorizedAnswerExecution
from tap.modules.knowledge.api import AuthorizedAnswerQuery as _AuthorizedAnswerQuery
from tap.modules.knowledge.api import get_template as _get_template

PlannerCall = Callable[[PlanningInput, float], Awaitable[dict[str, Any]]]
_TEMPLATE = {
    "general_chat": "general",
    "transform_text": "general",
    "factual_lookup": "factual",
    "explanation": "explanation",
    "comparison": "comparison",
    "procedural": "procedural",
    "clarification": "clarification",
}


def authorized_execution(plan: AnswerPlan) -> _AuthorizedAnswerExecution:
    if plan.route != "retrieve":
        raise ValueError("only admitted retrieval plans may reach Knowledge")
    remaining = plan.deadline_at - time.time()
    if remaining <= 0:
        raise TimeoutError("persisted answer budget exhausted")
    return _AuthorizedAnswerExecution(
        plan.plan_id,
        plan.project_id,
        plan.acl_digest,
        plan.model_alias,
        plan.original_question,
        plan.standalone_query,
        tuple(
            _AuthorizedAnswerQuery(
                item.id, item.text, item.depends_on, item.source_ids, item.evidence_goal
            )
            for item in plan.queries
        ),
        plan.template_id,
        plan.template_version,
        plan.template_digest,
        plan.candidate_limit,
        plan.evidence_limit,
        remaining,
        plan.output_requirements,
    )


def planning_input(
    snapshot: TurnInputSnapshot, *, history: tuple[TurnInputSnapshot, ...] = ()
) -> PlanningInput:
    value = snapshot.value
    allowed = {
        (item.source_id, item.revision_id, item.source_content_hash)
        for item in value.resolved_resources
    }
    recent = tuple(
        item
        for item in history[-8:]
        if (
            item.turn_id != snapshot.turn_id
            and item.project_id == snapshot.project_id
            and item.value.actor_id == value.actor_id
            and item.value.acl_digest == value.acl_digest
            and item.value.retrieval_policy_digest == value.retrieval_policy_digest
            and item.value.resolved_resources
            and {
                (source.source_id, source.revision_id, source.source_content_hash)
                for source in item.value.resolved_resources
            }
            <= allowed
            and re.search(r"上一版|previous version", item.value.message, re.I)
            and len(set(re.findall(r"\bv\d+(?:\.\d+)*\b", item.value.message, re.I))) >= 2
        )
    )
    return PlanningInput(
        project_id=snapshot.project_id,
        turn_id=snapshot.turn_id,
        input_digest=snapshot.digest,
        acl_digest=value.acl_digest,
        policy_digest=value.retrieval_policy_digest,
        original_question=value.message,
        model_alias=value.model_alias,
        source_ids=tuple(item.source_id for item in value.resolved_resources),
        authorized_referents=tuple(dict.fromkeys(item.value.message for item in recent)),
        context_digests=tuple(item.digest for item in recent),
    )


class AnswerPlanner:
    def __init__(self, model: PlannerCall | None = None):
        self._model = model

    async def plan(
        self, value: PlanningInput, *, previous_plan_id: str | None = None
    ) -> AnswerPlan:
        original = value.original_question
        deadline = time.time() + value.remaining_seconds
        standalone = original
        route, intent = "retrieve", "factual_lookup"
        missing: tuple[str, ...] = ()
        reason = None
        confidence = 1.0
        ambiguous = bool(re.search(r"它|上一版|\b(?:it|previous version)\b", original, re.I))
        complex_query = bool(re.search(r"比较|不同|对比|跨章节|compare|difference", original, re.I))
        direct = bool(
            re.fullmatch(r"\s*(?:你好|您好|hi|hello|谢谢|thanks)[！!。.\s]*", original, re.I)
        )
        transform = bool(
            re.match(r"(?:改写|润色|翻译|rewrite|translate).*[：:]\s*\S", original, re.I | re.S)
        )
        if direct or transform:
            route, intent = "direct", "transform_text" if transform else "general_chat"
        elif re.search(r"失败率|成功率|\bfailure rate\b|\bsuccess rate\b", original, re.I):
            route, reason = "insights", "capability-unavailable"
        elif re.search(r"完整表格|全部表格|entire (?:table|file)|full spreadsheet", original, re.I):
            route, reason = "file_analysis", "capability-unavailable"
        elif ambiguous and len(value.authorized_referents) != 1:
            route, intent, missing = "clarify", "clarification", ("object-or-version",)
        elif not value.source_ids:
            route, intent, missing = "clarify", "clarification", ("sources",)
        else:
            if ambiguous:
                standalone = value.authorized_referents[0] + "：" + original
            if complex_query:
                intent = "comparison"
            elif re.search(r"如何|步骤|how to", original, re.I):
                intent = "procedural"
            elif re.search(r"为什么|解释|why|explain", original, re.I):
                intent = "explanation"

        def build(queries=None):
            template = _get_template(_TEMPLATE[intent], "2" if intent == "clarification" else "1")
            return AnswerPlan(
                plan_id=uuid4().hex,
                project_id=value.project_id,
                turn_id=value.turn_id,
                input_digest=value.input_digest,
                acl_digest=value.acl_digest,
                policy_digest=value.policy_digest,
                model_alias=value.model_alias,
                original_question=original,
                intent=intent,
                route=route,
                confidence=confidence,
                standalone_query=standalone,
                constraints=protected_constraints(original),
                missing=tuple(missing),
                queries=tuple(queries)
                if queries is not None
                else (
                    (PlannedQuery("q1", standalone, (), standalone, value.source_ids),)
                    if route == "retrieve"
                    else ()
                ),
                source_ids=value.source_ids,
                template_id=template.id,
                template_version=template.version,
                template_digest=template.digest,
                deadline_at=deadline,
                candidate_limit=value.candidate_limit,
                evidence_limit=value.evidence_limit,
                remaining_seconds=value.remaining_seconds,
                output_requirements=value.output_requirements,
                previous_plan_id=previous_plan_id,
                degradation_reason=reason,
                context_digests=value.context_digests,
            )

        if route != "retrieve" or not complex_query or self._model is None or ambiguous:
            return build()
        timeout = min(3.0, value.remaining_seconds)
        if timeout <= 0:
            raise TimeoutError("answer budget exhausted")
        try:
            async with asyncio.timeout(timeout):
                raw = await self._model(value, timeout)
            if set(raw) != {
                "intent",
                "route",
                "confidence",
                "standalone_query",
                "missing",
                "queries",
            }:
                raise ValueError("invalid planning schema")
            if raw["route"] not in {"retrieve", "clarify"}:
                raise ValueError("model cannot authorize direct or capability routes")
            if (
                type(raw["confidence"]) not in {int, float}
                or not isinstance(raw["missing"], list)
                or not all(isinstance(item, str) for item in raw["missing"])
                or not isinstance(raw["queries"], list)
                or not isinstance(raw["standalone_query"], str)
            ):
                raise ValueError("invalid planning field types")
            for query in raw["queries"]:
                for field in ("depends_on", "source_ids"):
                    if not isinstance(query[field], list) or not all(
                        isinstance(item, str) for item in query[field]
                    ):
                        raise ValueError("invalid query field types")
            if set(protected_constraints(raw["standalone_query"])) != set(
                protected_constraints(original)
            ):
                raise ValueError("model changed exact constraints")
            # Token presence cannot prove which object a negation/date/version modifies.
            # Until there is a trusted proposition verifier, retain the entire original
            # expression for constrained questions instead of admitting a semantic rewrite.
            if protected_constraints(original) and (
                raw["standalone_query"] != original
                or any(
                    query["text"] != original or query["evidence_goal"] != original
                    for query in raw["queries"]
                )
            ):
                raise ValueError("model changed constraint associations")
            if (
                set(protected_constraints(" ".join(query["text"] for query in raw["queries"])))
                != set(protected_constraints(original))
                and raw["route"] == "retrieve"
            ):
                raise ValueError("model queries changed exact constraints")
            queries = tuple(
                PlannedQuery(
                    **{
                        **query,
                        "depends_on": tuple(query["depends_on"]),
                        "source_ids": tuple(query["source_ids"]),
                    }
                )
                for query in raw["queries"]
            )
            intent, route, confidence = raw["intent"], raw["route"], raw["confidence"]
            standalone, missing = raw["standalone_query"], tuple(raw["missing"])
            return build(queries)
        except TimeoutError:
            reason = "planner-timeout"
        except (ValueError, TypeError, KeyError):
            reason = "invalid-plan"
        except Exception:
            reason = "planner-unavailable"
        route, intent, standalone, confidence, missing = "retrieve", "comparison", original, 1.0, ()
        return build()
