"""Planning must fail closed before any search or generation can run."""

import importlib.util
from dataclasses import replace

import pytest


def planning():
    name = "tap.modules.chat.application.plan_answer"
    assert importlib.util.find_spec(name) is not None, "answer planning is not implemented"
    return __import__(name, fromlist=["AnswerPlanner"])


def context(message, **changes):
    module = planning()
    return module.PlanningInput(
        project_id="project-a",
        turn_id="turn-a",
        input_digest="sha256:" + "a" * 64,
        acl_digest="sha256:" + "b" * 64,
        policy_digest="sha256:" + "c" * 64,
        original_question=message,
        model_alias="qwen-plus",
        source_ids=("source-a",),
        **changes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,route,count",
    [
        ("你好", "direct", 0),
        ("改写以下段落：系统现在可以使用。", "direct", 0),
        ("错误码 E104 如何处理？", "retrieve", 1),
        ("它和上一版有什么不同？", "clarify", 0),
        ("最近 30 天失败率", "insights", 0),
        ("完整表格汇总", "file_analysis", 0),
    ],
)
async def test_explicit_routes_do_not_spend_planning_calls(message, route, count):
    module = planning()

    async def forbidden(_input, _timeout):
        pytest.fail("explicit input must not call the planning model")

    plan = await module.AnswerPlanner(forbidden).plan(context(message))
    assert plan.route == route
    assert len(plan.queries) == count
    if count:
        assert plan.queries[0].text == "错误码 E104 如何处理？"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["你好", "错误码 E104 如何处理？", "它和上一版有什么不同？"])
async def test_new_plans_pin_the_latest_answer_template_version(message):
    from tap.modules.knowledge.application.answer_templates import (
        get_template,
        latest_template_version,
    )

    async def forbidden(_input, _timeout):
        pytest.fail("explicit input must not call the planning model")

    plan = await planning().AnswerPlanner(forbidden).plan(context(message))
    assert plan.template_version == latest_template_version(plan.template_id)
    assert plan.template_digest == get_template(plan.template_id, plan.template_version).digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,route",
    [
        (
            "How many days does compliance approval take, and who is authorized to grant it?",
            "retrieve",
        ),
        ("Is it different from the previous version?", "clarify"),
        ("How does it differ from v1?", "clarify"),
    ],
)
async def test_english_it_is_ambiguous_only_as_an_unresolved_version_referent(message, route):
    async def forbidden(_input, _timeout):
        pytest.fail("explicit input must not call the planning model")

    plan = await planning().AnswerPlanner(forbidden).plan(context(message))
    assert plan.route == route


@pytest.mark.asyncio
async def test_enterprise_fact_without_sources_never_becomes_direct_chat():
    plan = (
        await planning()
        .AnswerPlanner()
        .plan(replace(context("我们公司的报销政策是什么？"), source_ids=()))
    )
    assert plan.route == "clarify"
    assert "sources" in plan.missing


@pytest.mark.asyncio
async def test_unique_lineage_resolves_reference_without_using_history_as_evidence():
    module = planning()
    value = context("它和上一版有什么不同？", authorized_referents=("支付规范 v2 与 v1",))
    plan = await module.AnswerPlanner().plan(value)
    assert plan.route == "retrieve"
    assert "支付规范 v2 与 v1" in plan.standalone_query
    assert plan.source_ids == ("source-a",)


def suggestion(**changes):
    return {
        "intent": "comparison",
        "route": "retrieve",
        "confidence": 0.9,
        "standalone_query": "比较章节 A 和 B",
        "missing": [],
        "queries": [
            {
                "id": "q1",
                "text": "章节 A",
                "depends_on": [],
                "evidence_goal": "A",
                "source_ids": ["source-a"],
            },
            {
                "id": "q2",
                "text": "章节 B",
                "depends_on": ["q1"],
                "evidence_goal": "B",
                "source_ids": ["source-a"],
            },
        ],
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"intent": "unknown"},
        {"route": "execute"},
        {
            "queries": [
                {
                    "id": f"q{i}",
                    "text": "x",
                    "depends_on": [],
                    "evidence_goal": "x",
                    "source_ids": ["source-a"],
                }
                for i in range(4)
            ]
        },
        {
            "queries": [
                {
                    "id": "q1",
                    "text": "x",
                    "depends_on": ["q1"],
                    "evidence_goal": "x",
                    "source_ids": ["source-a"],
                }
            ]
        },
        {
            "queries": [
                {
                    "id": "q1",
                    "text": "x",
                    "depends_on": [],
                    "evidence_goal": "x",
                    "source_ids": ["other-project"],
                }
            ]
        },
        {"acl_digest": "model-controlled"},
    ],
)
async def test_invalid_model_suggestions_fall_back_to_one_original_query(bad):
    async def model(_input, timeout):
        assert 0 < timeout <= 3
        return suggestion(**bad)

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"
    assert [query.text for query in plan.queries] == ["比较章节 A 和 B"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,rewrite",
    [
        ("比较 E104 和 E105", "比较 E104 和 E106"),
        ("比较不允许导出的 A 和 B", "比较允许导出的 A 和 B"),
        ("比较 2026-01-02 v2 和 v3", "比较 2026-01-03 v2 和 v3"),
        ("比较 E104 和 E105", "比较 E104X 和 E105"),
    ],
)
async def test_identifier_negation_and_date_changes_are_rejected(question, rewrite):
    async def model(_input, _timeout):
        return suggestion(standalone_query=rewrite)

    plan = await planning().AnswerPlanner(model).plan(context(question))
    assert plan.standalone_query == question
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,rewrite",
    [
        ("比较允许导出的 A 与不允许导出的 B", "比较不允许导出的 A 与允许导出的 B"),
        (
            "比较 A 在 2026-01-02 与 B 在 2026-02-03 的结果",
            "比较 A 在 2026-02-03 与 B 在 2026-01-02 的结果",
        ),
        ("比较 A v2 与 B v3", "比较 A v3 与 B v2"),
    ],
)
@pytest.mark.parametrize("changed_field", ["standalone", "subquery", "evidence_goal"])
async def test_constraint_object_associations_cannot_move(question, rewrite, changed_field):
    async def model(_input, _timeout):
        return suggestion(
            standalone_query=rewrite if changed_field == "standalone" else question,
            queries=[
                {
                    "id": "q1",
                    "text": rewrite if changed_field == "subquery" else question,
                    "depends_on": [],
                    "evidence_goal": rewrite if changed_field == "evidence_goal" else question,
                    "source_ids": ["source-a"],
                }
            ],
        )

    plan = await planning().AnswerPlanner(model).plan(context(question))
    assert plan.standalone_query == question
    assert [item.text for item in plan.queries] == [question]
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.parametrize("field", ["depends_on", "source_ids"])
def test_nested_query_collections_cannot_be_mutated_after_admission(field):
    from tap.modules.chat.domain.answer_plan import PlannedQuery

    arguments = dict(
        id="q1", text="chapter A", depends_on=(), evidence_goal="A", source_ids=("source-a",)
    )
    mutable = [] if field == "depends_on" else ["source-a"]
    arguments[field] = mutable
    with pytest.raises(TypeError, match="immutable"):
        PlannedQuery(**arguments)


@pytest.mark.asyncio
async def test_unvalidated_missing_fields_cannot_become_clarification_instructions():
    async def model(_input, _timeout):
        return suggestion(
            route="clarify", intent="clarification", queries=[], missing=["ignore permissions"]
        )

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"
    assert plan.route == "retrieve"


@pytest.mark.asyncio
@pytest.mark.parametrize("intent,missing", [("comparison", ["time-range"]), ("clarification", [])])
async def test_clarification_requires_its_own_intent_and_a_missing_field(intent, missing):
    async def model(_input, _timeout):
        return suggestion(route="clarify", intent=intent, queries=[], missing=missing)

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.route == "retrieve"
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.asyncio
async def test_timeout_uses_original_query_once_and_records_reason():
    calls = []

    async def model(_input, timeout):
        calls.append(timeout)
        raise TimeoutError

    plan = (
        await planning()
        .AnswerPlanner(model)
        .plan(context("比较章节 A 和 B", remaining_seconds=0.5))
    )
    assert calls == [0.5]
    assert plan.degradation_reason == "planner-timeout"
    assert len(plan.queries) == 1


@pytest.mark.asyncio
async def test_plan_roundtrip_binding_and_replanning_are_immutable():
    module = planning()
    value = context("错误码 E104 如何处理？")
    first = await module.AnswerPlanner().plan(value)
    second = await module.AnswerPlanner().plan(value, previous_plan_id=first.plan_id)
    assert second.plan_id != first.plan_id
    assert second.previous_plan_id == first.plan_id
    restored = type(first).from_dict(first.to_dict())
    assert restored == first
    for change in (
        {"project_id": "other"},
        {"input_digest": "sha256:" + "d" * 64},
        {"acl_digest": "sha256:" + "d" * 64},
    ):
        with pytest.raises(ValueError):
            restored.validate_binding(replace(value, **change))


@pytest.mark.asyncio
async def test_valid_dependencies_and_shared_budget_are_retained():
    async def model(_input, _timeout):
        return suggestion()

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.queries[1].depends_on == ("q1",)
    assert plan.candidate_limit == 20
    assert plan.evidence_limit == 10
    assert plan.degradation_reason is None


@pytest.mark.asyncio
async def test_structured_adapter_uses_closed_schema_and_disables_transport_retries():
    name = "tap.modules.chat.adapters.model_gateway_planner"
    assert importlib.util.find_spec(name) is not None, "structured planning adapter missing"
    module = __import__(name, fromlist=["ModelGatewayPlanner"])
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE

    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return SimpleNamespace(output=suggestion())

    async def redact(text):
        return text

    adapter = module.ModelGatewayPlanner(Gateway(), scope=VALIDATION_SCOPE, redact=redact)
    result = await adapter(context("比较章节 A 和 B"), 0.5)
    assert result == suggestion()
    assert requests[0].schema["additionalProperties"] is False
    assert requests[0].schema["properties"]["queries"]["maxItems"] == 3
    assert requests[0].allow_retries is False
    assert requests[0].timeout_seconds == 0.5


def test_planner_schema_is_accepted_by_gateway_and_enforces_bounds():
    from tap.modules.ai.application.schema import check_schema, validate_output
    from tap.modules.chat.adapters.model_gateway_planner import PLANNER_SCHEMA

    check_schema(PLANNER_SCHEMA)
    validate_output(PLANNER_SCHEMA, suggestion())
    for invalid in (suggestion(confidence=2), suggestion(queries=suggestion()["queries"] * 2)):
        with pytest.raises(ValueError):
            validate_output(PLANNER_SCHEMA, invalid)


@pytest.mark.asyncio
async def test_model_cannot_append_to_exact_identifiers_in_otherwise_valid_queries():
    async def model(_input, _timeout):
        return suggestion(
            standalone_query="比较 E104X 和 E105",
            queries=[
                {
                    "id": "q1",
                    "text": "比较 E104X 和 E105",
                    "depends_on": [],
                    "evidence_goal": "compare",
                    "source_ids": ["source-a"],
                }
            ],
        )

    plan = await planning().AnswerPlanner(model).plan(context("比较 E104 和 E105"))
    assert plan.degradation_reason == "invalid-plan"
    assert plan.queries[0].text == "比较 E104 和 E105"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"missing": "object"},
        {"confidence": True},
        {
            "queries": [
                {
                    "id": "q1",
                    "text": "章节 A",
                    "depends_on": "",
                    "source_ids": ["source-a"],
                    "evidence_goal": "A",
                }
            ]
        },
    ],
)
async def test_malformed_structured_types_cannot_be_coerced_into_a_plan(bad):
    async def model(_input, _timeout):
        return suggestion(**bad)

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"plan_id": ""}, {"queries": []}, {"source_ids": ["source-a"]}, {"candidate_limit": True}],
)
async def test_plan_cannot_be_constructed_with_mutable_or_invalid_authority(changes):
    plan = await planning().AnswerPlanner().plan(context("错误码 E104 如何处理？"))
    with pytest.raises((TypeError, ValueError)):
        replace(plan, **changes)


@pytest.mark.asyncio
async def test_plan_constructor_rejects_identifier_suffix_mutation():
    from tap.modules.chat.domain.answer_plan import PlannedQuery

    plan = await planning().AnswerPlanner().plan(context("错误码 E104 如何处理？"))
    with pytest.raises(ValueError):
        replace(
            plan,
            standalone_query="错误码 E104X 如何处理？",
            queries=(PlannedQuery("q1", "E104X", (), "error", plan.source_ids),),
        )


@pytest.mark.asyncio
async def test_recent_user_context_requires_matching_authorized_source_lineage():
    from datetime import datetime, timezone

    from tap.modules.chat.domain.conversations import FrozenResource, TurnInput, TurnInputSnapshot

    module = planning()
    resource = FrozenResource("source-a", "doc-a", "rev-a", "sha256:" + "d" * 64)

    def snapshot(identity, message, selected=resource):
        value = TurnInput(
            message, "actor-a", "validation", "qwen-plus", resolved_resources=(selected,)
        )
        return TurnInputSnapshot.create(
            snapshot_id=identity,
            project_id="project-a",
            turn_id=identity,
            value=value,
            now=datetime.now(timezone.utc),
        )

    current = snapshot("current", "它和上一版有什么不同？")
    previous = snapshot("previous", "支付规范 v2，上一版是 v1")
    import inspect

    assert "history" in inspect.signature(module.planning_input).parameters, (
        "planner does not consume authorized recent user context"
    )
    value = module.planning_input(current, history=(previous,))
    plan = await module.AnswerPlanner().plan(value)
    assert plan.route == "retrieve"
    assert "支付规范 v2，上一版是 v1" in plan.standalone_query
    other = snapshot("other", "其他规范 v2，上一版是 v1", replace(resource, source_id="source-b"))
    rejected = await module.AnswerPlanner().plan(module.planning_input(current, history=(other,)))
    assert rejected.route == "clarify"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "核保流程和健康告知是什么关系",
        "哪些规则影响理赔时效",
        "What is the relationship between underwriting and claims",
    ],
)
async def test_relation_questions_route_to_graph_with_queries(message):
    module = planning()

    async def forbidden(_input, _timeout):
        pytest.fail("relation heuristic must not call the planning model")

    plan = await module.AnswerPlanner(forbidden).plan(context(message))
    assert plan.intent == "relation"
    assert plan.route == "graph"
    assert len(plan.queries) == 1
    assert plan.template_id == "explanation"


@pytest.mark.asyncio
async def test_graph_route_requires_queries_like_retrieve():
    plan = await planning().AnswerPlanner().plan(context("核保流程和健康告知是什么关系"))
    assert plan.route == "graph"
    assert len(plan.queries) == 1
    with pytest.raises(ValueError):
        replace(plan, route="graph", queries=())


@pytest.mark.asyncio
async def test_authorized_execution_accepts_graph_route_and_carries_intent():
    module = planning()
    plan = await module.AnswerPlanner().plan(context("核保流程和健康告知是什么关系"))
    assert plan.route == "graph"
    execution = module.authorized_execution(plan)
    assert execution.intent == "relation"


def test_planner_schema_lists_relation_intent_and_graph_route():
    from tap.modules.chat.adapters.model_gateway_planner import PLANNER_SCHEMA

    assert "relation" in PLANNER_SCHEMA["properties"]["intent"]["enum"]
    assert "graph" in PLANNER_SCHEMA["properties"]["route"]["enum"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,intent",
    [("retrieve", "comparison"), ("graph", "relation")],
)
async def test_model_injected_constraint_in_query_text_falls_back_on_every_route(route, intent):
    """Review I1: the query-constraint equality check must not be skipped for
    the graph route -- a model-returned plan that injects a negation/version
    into a query's text (absent from the original question entirely) must
    fall back to `invalid-plan` on every admitted route, not just retrieve."""

    async def model(_input, _timeout):
        return suggestion(
            intent=intent,
            route=route,
            standalone_query="compare underwriting and claims",
            queries=[
                {
                    "id": "q1",
                    "text": "underwriting claims not v3",
                    "depends_on": [],
                    "evidence_goal": "underwriting claims not v3",
                    "source_ids": ["source-a"],
                }
            ],
        )

    plan = await planning().AnswerPlanner(model).plan(context("compare underwriting and claims"))
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.asyncio
async def test_relation_word_compound_noun_keeps_procedural_intent():
    """Review fix round 3/5: `劳动关系证明` is a document name (`关系证明`), not a
    relation question -- the question must still resolve to the ordinary
    `procedural` intent `如何` would otherwise produce, not merely avoid
    `relation`/`graph`."""

    plan = await planning().AnswerPlanner().plan(context("如何办理劳动关系证明"))
    assert plan.intent == "procedural"
    assert plan.route == "retrieve"


@pytest.mark.asyncio
async def test_ordinary_procedural_question_does_not_route_to_graph():
    plan = await planning().AnswerPlanner().plan(context("如何提交理赔申请"))
    assert plan.intent == "procedural"
    assert plan.route == "retrieve"


@pytest.mark.asyncio
async def test_ordinary_comparison_question_does_not_route_to_graph():
    plan = await planning().AnswerPlanner(None).plan(context("比较核保流程和理赔流程的不同"))
    assert plan.intent == "comparison"
    assert plan.route == "retrieve"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_changes",
    [
        {"intent": "relation", "route": "retrieve"},
        {"intent": "comparison", "route": "graph"},
    ],
)
async def test_model_intent_and_route_must_pair_for_relation_and_graph(raw_changes):
    """Review M2: intent=relation and route=graph must be admitted together or
    not at all; a mismatched model plan falls back the same way other invalid
    model plans do."""

    async def model(_input, _timeout):
        return suggestion(**raw_changes)

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message,should_route_to_graph",
    [
        # Fix round 2/5: recall -- natural phrasings that must reach relation/graph.
        ("A和B之间的关系", True),  # regression: bare 关系 after 之间 with no 什么/有/是
        ("A和B有什么关系", True),
        ("A与B是什么关系", True),
        ("A和B的关系是什么", True),
        ("A会影响B吗", True),
        ("A影响哪些流程", True),
        ("A依赖什么", True),
        ("A的前置条件是什么", True),
        ("A的上游是什么", True),
        ("提交后下一步是什么", True),
        ("核保之后是什么步骤", True),
        ("what is the relationship between A and B", True),
        ("how does A relate to B", True),
        ("How are A and B related?", True),
        ("What depends on A?", True),
        ("Does A affect B?", True),
        ("which systems affect claims", True),
        ("What comes after underwriting?", True),
        ("what follows underwriting", True),  # regression: no "?"
        # Fix round 3/5: recall -- English inflections (relates/related/depends/
        # dependency/affected/impacted/triggers), still gated on question form.
        ("What triggers a policy lapse?", True),
        ("What affects the premium?", True),
        ("Which rules are affected by underwriting?", True),
        ("Is my premium affected by smoking?", True),
        ("How does A relates to B?", True),
        ("Is there a dependency between A and B?", True),
        ("Which processes are impacted by underwriting?", True),
        # Fix round 3/5: recall -- 联系/关联 and additional sequence phrasings.
        ("A和B有什么联系", True),
        ("A和B有联系吗", True),
        ("核保和理赔怎么关联", True),
        ("核保后下一步做什么", True),
        ("核保完成后接下来是什么", True),
        ("What happens after underwriting?", True),
        ("What is the next step after submission?", True),
        # Fix round 3/5: precision retained -- bare 关系 stays positive.
        ("投保人与被保险人是什么关系", True),
        # Fix round 2/5: precision -- lookalikes that must not reach relation/graph.
        ("提交申请后下一步是否需要审批", False),
        ("两个系统之间的关系型数据库配置", False),
        ("这条规则与审批有什么关系型约束", False),
        ("他们是什么关系户", False),
        ("What follows is a list of rules", False),
        ("What follows is a list of rules?", False),
        ("What follows is a list of rules.", False),  # merged from the round-1 M1 test
        ("如何提交理赔申请", False),
        ("比较核保流程和理赔流程的不同", False),
        ("哪些字段是必填的", False),
        ("which of these documents are PDFs", False),
        # Fix round 3/5: precision -- 关系 compound nouns and 没关系.
        ("亲属关系证明怎么开", False),
        ("如何办理劳动关系证明", False),
        ("客户关系管理系统怎么登录", False),
        ("关系人信息在哪里填写", False),
        ("没关系，继续", False),
        # Fix round 3/5: precision -- "is|are" after "what follows", and bare 联系.
        ("What follows are the exclusions", False),
        ("What follows is an overview", False),
        ("What follows is the list of rules", False),
        ("联系电话是多少", False),
        ("联系方式在哪里", False),
    ],
)
async def test_relation_heuristic_precision_and_recall_table(message, should_route_to_graph):
    """Review fix rounds 2/5 and 3/5: a demo-facing, table-driven check of the
    relation heuristic's recall (natural question phrasings in Chinese and
    English, including inflected English forms and 联系/关联 phrasings added
    in round 3) and precision (lookalikes, including 关系 compound nouns and
    "what follows is/are" declaratives, that must stay off the graph route)
    in one place, so the whole acceptance surface is visible and
    regression-checked together rather than split across ad hoc single-case
    tests."""

    plan = await planning().AnswerPlanner().plan(context(message))
    if should_route_to_graph:
        assert plan.intent == "relation", message
        assert plan.route == "graph", message
    else:
        assert plan.intent != "relation", message
        assert plan.route != "graph", message
