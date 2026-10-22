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
        ("它和上一版有什么不同？", "clarify", 0),
        ("最近 30 天失败率", "insights", 0),
        ("完整表格汇总", "file_analysis", 0),
    ],
)
async def test_explicit_routes_do_not_spend_planning_calls(message, route, count):
    """Deterministic shortcuts (greeting/rewrite `direct`, `clarify`, and the
    capability-unavailable `insights`/`file_analysis` routes) never consult
    the model -- only `retrieve`/`graph` do, every turn, as of this task."""
    module = planning()

    async def forbidden(_input, _timeout):
        pytest.fail("explicit input must not call the planning model")

    plan = await module.AnswerPlanner(forbidden).plan(context(message))
    assert plan.route == route
    assert len(plan.queries) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["你好", "错误码 E104 如何处理？", "它和上一版有什么不同？"])
async def test_new_plans_pin_the_latest_answer_template_version(message):
    from tap.modules.knowledge.application.answer_templates import (
        get_template,
        latest_template_version,
    )

    plan = await planning().AnswerPlanner(None).plan(context(message))
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
    plan = await planning().AnswerPlanner(None).plan(context(message))
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
    base = {
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
    }
    merged = {**base, **changes}
    merged.setdefault(
        "agent_intent", "out_of_scope" if merged["intent"] == "out_of_scope" else "multi_step_qa"
    )
    merged.setdefault("intent_reason", "")
    return merged


@pytest.mark.asyncio
async def test_model_out_of_scope_intent_requires_direct_route_without_queries():
    async def direct_model(_input, _timeout):
        return suggestion(intent="out_of_scope", route="direct", queries=[])

    plan = await planning().AnswerPlanner(direct_model).plan(context("比较章节 A 和 B"))
    assert plan.intent == "out_of_scope"
    assert plan.route == "direct"
    assert plan.queries == ()
    assert plan.degradation_reason is None

    async def retrieve_model(_input, _timeout):
        return suggestion(intent="out_of_scope", route="retrieve")

    degraded = await planning().AnswerPlanner(retrieve_model).plan(context("比较章节 A 和 B"))
    assert degraded.degradation_reason is not None
    assert degraded.intent != "out_of_scope"


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
        # A blank restatement is only admitted where nothing is searched
        # (direct/clarify); retrieval plans keep requiring one, and queries.
        {"standalone_query": ""},
        {"standalone_query": "  "},
        {"queries": []},
        {"intent": "relation", "route": "graph", "standalone_query": ""},
        {"intent": "relation", "route": "graph", "queries": []},
    ],
)
async def test_invalid_model_suggestions_fall_back_to_one_original_query(bad):
    async def model(_input, timeout):
        assert 0 < timeout <= planning().DEFAULT_PLANNER_TIMEOUT_SECONDS
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

    plan = await module.AnswerPlanner(None).plan(context(message))
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
    """The query-constraint equality check must not be skipped for
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
    """`劳动关系证明` is a document name (`关系证明`), not a
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
    """Intent=relation and route=graph must be admitted together or
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
        # recall -- natural phrasings that must reach relation/graph.
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
        # recall -- English inflections (relates/related/depends/
        # dependency/affected/impacted/triggers), still gated on question form.
        ("What triggers a policy lapse?", True),
        ("What affects the premium?", True),
        ("Which rules are affected by underwriting?", True),
        ("Is my premium affected by smoking?", True),
        ("How does A relates to B?", True),
        ("Is there a dependency between A and B?", True),
        ("Which processes are impacted by underwriting?", True),
        # recall -- 联系/关联 and additional sequence phrasings.
        ("A和B有什么联系", True),
        ("A和B有联系吗", True),
        ("核保和理赔怎么关联", True),
        ("核保后下一步做什么", True),
        ("核保完成后接下来是什么", True),
        ("What happens after underwriting?", True),
        ("What is the next step after submission?", True),
        # precision retained -- bare 关系 stays positive.
        ("投保人与被保险人是什么关系", True),
        # precision -- lookalikes that must not reach relation/graph.
        ("提交申请后下一步是否需要审批", False),
        ("两个系统之间的关系型数据库配置", False),
        ("这条规则与审批有什么关系型约束", False),
        ("他们是什么关系户", False),
        ("What follows is a list of rules", False),
        ("What follows is a list of rules?", False),
        ("What follows is a list of rules.", False),  # trailing punctuation variant
        ("如何提交理赔申请", False),
        ("比较核保流程和理赔流程的不同", False),
        ("哪些字段是必填的", False),
        ("which of these documents are PDFs", False),
        # precision -- 关系 compound nouns and 没关系.
        ("亲属关系证明怎么开", False),
        ("如何办理劳动关系证明", False),
        ("客户关系管理系统怎么登录", False),
        ("关系人信息在哪里填写", False),
        ("没关系，继续", False),
        # precision -- "is|are" after "what follows", and bare 联系.
        ("What follows are the exclusions", False),
        ("What follows is an overview", False),
        ("What follows is the list of rules", False),
        ("联系电话是多少", False),
        ("联系方式在哪里", False),
        # precision -- 联系/关联/上游/下游 lookalikes without an
        # entity-pair link question, and "next step" without "after".
        ("怎么联系客服", False),
        ("怎么和客服联系", False),
        ("联系人怎么填写", False),
        ("联系人电话是什么", False),
        ("关联账户怎么解绑", False),
        ("怎么关联银行卡", False),
        ("下游系统怎么配置", False),
        ("上游接口怎么调用", False),
        ("next step for my claim?", False),
        # recall -- entity-pair link questions.
        ("A与B有什么关联", True),
        ("A和B有没有联系？", True),
    ],
)
async def test_relation_heuristic_precision_and_recall_table(message, should_route_to_graph):
    """A demo-facing, table-driven check of the
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


@pytest.mark.asyncio
async def test_model_plan_carries_agent_intent_and_reason():
    async def model(_input, _timeout):
        return suggestion(agent_intent="multi_step_qa", intent_reason="needs two lookups")

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.agent_intent == "multi_step_qa"
    assert plan.intent_reason == "needs two lookups"


@pytest.mark.asyncio
async def test_simple_question_now_consults_the_model_planner():
    """Only deterministic shortcuts skip the model: a non-comparison
    question with sources selected must still reach the planner model on the
    `retrieve` route, every turn -- not only for `complex_query` intents."""
    calls = []

    async def model(_input, _timeout):
        calls.append(1)
        return suggestion()

    plan = await planning().AnswerPlanner(model).plan(context("出险后几天内报案？"))
    assert len(calls) == 1
    assert plan.route == "retrieve"


@pytest.mark.asyncio
async def test_deterministic_shortcuts_skip_model_and_derive_intent():
    async def forbidden(_input, _timeout):
        pytest.fail("deterministic shortcut must not call the planning model")

    module = planning()
    greeting = await module.AnswerPlanner(forbidden).plan(context("你好"))
    assert greeting.route == "direct"
    assert greeting.agent_intent == "knowledge_qa"

    missing_sources = await module.AnswerPlanner(forbidden).plan(
        replace(context("我们公司的报销政策是什么？"), source_ids=())
    )
    assert missing_sources.route == "clarify"
    assert missing_sources.agent_intent == "clarify"


@pytest.mark.asyncio
async def test_agent_intent_out_of_scope_must_pair_with_legacy_intent():
    async def model(_input, _timeout):
        return suggestion(intent="factual_lookup", route="retrieve", agent_intent="out_of_scope")

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"
    assert plan.agent_intent != "out_of_scope"


async def _planner_raises_timeout(_input, _timeout):
    raise TimeoutError


async def _planner_returns_invalid_plan(_input, _timeout):
    return {"intent": "relation"}


async def _planner_raises_unexpected(_input, _timeout):
    raise RuntimeError("provider down")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,reason",
    [
        (_planner_raises_timeout, "planner-timeout"),
        (_planner_returns_invalid_plan, "invalid-plan"),
        (_planner_raises_unexpected, "planner-unavailable"),
    ],
)
@pytest.mark.parametrize(
    "message",
    ["核保流程和健康告知是什么关系", "错误码 E104 如何处理？", "保单的等待期是多久"],
)
async def test_planner_model_failure_keeps_the_heuristic_plan(model, reason, message):
    """Final review I-1: a planner-model failure must degrade to the plan the
    deterministic heuristic already computed (graph route for a relation
    question, its own intent for a simple one), not re-plan every Turn as a
    `comparison` retrieve -- only `degradation_reason` records the failure."""
    module = planning()
    heuristic = await module.AnswerPlanner(None).plan(context(message))

    plan = await module.AnswerPlanner(model).plan(context(message))

    assert plan.degradation_reason == reason
    assert plan.route == heuristic.route
    assert plan.intent == heuristic.intent
    assert plan.agent_intent == heuristic.agent_intent
    assert plan.standalone_query == heuristic.standalone_query
    assert plan.confidence == heuristic.confidence
    assert [query.text for query in plan.queries] == [query.text for query in heuristic.queries]


@pytest.mark.asyncio
async def test_production_planner_gateway_failure_keeps_relation_graph_route():
    """Final review I-1 through the production assembly
    (`AnswerPlanner(ModelGatewayPlanner(gateway))`, as `tapper_runtime`
    wires it): a gateway failure keeps the relation question's `graph`
    route instead of a `comparison` retrieve."""
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner

    class Gateway:
        async def generate_structured(self, request):
            raise RuntimeError("provider down")

    async def redact(text):
        return text

    planner = planning().AnswerPlanner(
        ModelGatewayPlanner(Gateway(), scope=VALIDATION_SCOPE, redact=redact)
    )
    plan = await planner.plan(context("核保流程和健康告知是什么关系"))

    assert plan.degradation_reason == "planner-unavailable"
    assert (plan.route, plan.intent) == ("graph", "relation")
    assert plan.agent_intent != "multi_step_qa"


@pytest.mark.asyncio
async def test_model_query_without_source_ids_searches_every_authorized_source():
    """Real-model probe: echoing every authorized source id into every query
    made the planner's output (and latency) grow with the number of attached
    documents (6 sources: 7.5-14 s). An empty `source_ids` list means "every
    authorized source"; only a nonempty list narrows the query."""
    queries = [{**query, "source_ids": []} for query in suggestion()["queries"]]

    async def model(_input, _timeout):
        return suggestion(queries=queries)

    value = replace(context("比较章节 A 和 B"), source_ids=("source-a", "source-b"))
    plan = await planning().AnswerPlanner(model).plan(value)
    assert plan.degradation_reason is None
    assert [query.source_ids for query in plan.queries] == [("source-a", "source-b")] * 2


@pytest.mark.asyncio
async def test_out_of_scope_plan_with_blank_standalone_query_is_admitted():
    """Real-model probe: qwen-plus returns `standalone_query: ""` for an
    out-of-scope request (there is nothing to search); that is a valid plan,
    not an invalid one, so the original question stands in for it."""

    async def model(_input, _timeout):
        return suggestion(intent="out_of_scope", route="direct", queries=[], standalone_query="")

    plan = await planning().AnswerPlanner(model).plan(context("帮我写一首关于春天的诗。"))
    assert plan.degradation_reason is None
    assert plan.agent_intent == "out_of_scope"
    assert plan.standalone_query == "帮我写一首关于春天的诗。"


@pytest.mark.asyncio
async def test_planner_budget_defaults_to_the_measured_real_model_budget():
    module = planning()
    timeouts = []

    async def model(_input, timeout):
        timeouts.append(timeout)
        return suggestion()

    await module.AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    await module.AnswerPlanner(model, timeout_seconds=4.5).plan(context("比较章节 A 和 B"))
    await module.AnswerPlanner(model, timeout_seconds=4.5).plan(
        context("比较章节 A 和 B", remaining_seconds=2.0)
    )
    assert timeouts == [module.DEFAULT_PLANNER_TIMEOUT_SECONDS, 4.5, 2.0]
    assert module.DEFAULT_PLANNER_TIMEOUT_SECONDS == 6.0


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_planner_budget_must_be_a_positive_finite_duration(value):
    with pytest.raises(ValueError):
        planning().AnswerPlanner(None, timeout_seconds=value)


@pytest.mark.asyncio
async def test_structured_adapter_passes_the_planner_budget_through():
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner

    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return SimpleNamespace(output=suggestion())

    async def redact(text):
        return text

    await ModelGatewayPlanner(Gateway(), scope=VALIDATION_SCOPE, redact=redact)(
        context("比较章节 A 和 B"), 6.0
    )
    assert requests[0].timeout_seconds == 6.0


def test_planner_prompt_carries_route_semantics_and_leaves_the_schema_to_the_gateway():
    """The provider's `json_schema` response format constrains decoding but
    is not shown to the model, so without route/intent meanings and the
    enums qwen-plus picked `intent` blind (`clarification`/`out_of_scope` on
    a `direct` route for most answerable questions). The gateway now appends
    the exact schema to every structured request's trusted instructions, so
    the planner prompt carries only the route semantics -- no second copy."""
    import json

    from tap.modules.chat.adapters.model_gateway_planner import PLANNER_PROMPT, PLANNER_SCHEMA
    from tap.modules.chat.domain.answer_plan import AGENT_INTENTS

    assert json.dumps(PLANNER_SCHEMA["properties"]["intent"]) not in PLANNER_PROMPT
    assert '"additionalProperties"' not in PLANNER_PROMPT
    for route in ("retrieve", "graph", "clarify", "direct"):
        assert f"- {route}" in PLANNER_PROMPT
    for agent_intent in AGENT_INTENTS:
        assert f"{agent_intent} (" in PLANNER_PROMPT or agent_intent in ("clarify", "out_of_scope")
    assert "empty source_ids" in PLANNER_PROMPT
    assert "compact single-line JSON" in PLANNER_PROMPT
    assert "insurance" not in PLANNER_PROMPT


_CONSTRAINED = "列出不超过5条的保单贷款测试检查点"


def _blank_constrained(**changes):
    return suggestion(
        intent="procedural",
        standalone_query="",
        queries=[
            {
                "id": "q1",
                "text": "",
                "depends_on": [],
                "evidence_goal": "",
                "source_ids": [],
            }
        ],
        **changes,
    )


@pytest.mark.asyncio
async def test_a_constrained_question_left_blank_is_filled_with_the_original_question():
    """N3: copying a long constrained question verbatim into standalone_query
    and every query text and evidence_goal took the planner past its 6 s
    budget; the model now leaves them empty and the code fills in the
    original question, so the plan is admitted without a semantic rewrite."""

    async def model(_input, _timeout):
        return _blank_constrained()

    plan = await planning().AnswerPlanner(model).plan(context(_CONSTRAINED))
    assert plan.degradation_reason is None
    assert plan.intent == "procedural"
    assert plan.standalone_query == _CONSTRAINED
    assert [(query.text, query.evidence_goal) for query in plan.queries] == [
        (_CONSTRAINED, _CONSTRAINED)
    ]
    assert plan.queries[0].source_ids == ("source-a",)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["standalone", "text", "evidence_goal"])
async def test_a_non_verbatim_value_for_a_constrained_question_is_still_rejected(field):
    raw = _blank_constrained()
    if field == "standalone":
        raw["standalone_query"] = "保单贷款测试检查点"
    else:
        raw["queries"][0][field] = "保单贷款测试检查点"

    async def model(_input, _timeout):
        return raw

    plan = await planning().AnswerPlanner(model).plan(context(_CONSTRAINED))
    assert plan.degradation_reason == "invalid-plan"
    assert plan.standalone_query == _CONSTRAINED


@pytest.mark.asyncio
async def test_blank_fields_for_an_unconstrained_question_are_still_rejected():
    async def model(_input, _timeout):
        return _blank_constrained()

    plan = await planning().AnswerPlanner(model).plan(context("比较章节 A 和 B"))
    assert plan.degradation_reason == "invalid-plan"


def test_planner_prompt_asks_constrained_questions_to_leave_restatements_empty():
    from tap.modules.chat.adapters.model_gateway_planner import PLANNER_PROMPT

    assert "copy the question verbatim" not in PLANNER_PROMPT
    assert (
        "When constrainedQuestion is true (the question contains a negation, number, date or "
        "version), return one query and leave standalone_query, its text and its evidence_goal "
        "empty" in PLANNER_PROMPT
    )
    assert "Otherwise standalone_query restates the question and is never empty" in PLANNER_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("message,flag", [(_CONSTRAINED, True), ("比较章节 A 和 B", False)])
async def test_planner_context_marks_a_constrained_question(message, flag):
    """qwen-plus kept paraphrasing constrained questions when told only "a
    question with a negation or number"; the code-computed flag is followed."""
    import json
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.chat.adapters.model_gateway_planner import ModelGatewayPlanner

    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return SimpleNamespace(output=suggestion())

    async def redact(text):
        return text

    await ModelGatewayPlanner(Gateway(), scope=VALIDATION_SCOPE, redact=redact)(
        context(message), 0.5
    )
    assert json.loads(requests[0].context)["constrainedQuestion"] is flag


@pytest.mark.asyncio
@pytest.mark.parametrize("message,constrained", [(_CONSTRAINED, True), ("比较章节 A 和 B", False)])
async def test_a_constrained_question_gets_a_schema_with_empty_restatements(message, constrained):
    """Even told to leave them empty, qwen-plus paraphrased the restatements
    of 5 of 9 constrained questions (all rejected as invalid plans); the
    schema it sees and decodes against fixes them to empty strings."""
    from types import SimpleNamespace

    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.domain.models import schema_digest
    from tap.modules.chat.adapters.model_gateway_planner import (
        PLANNER_SCHEMA,
        ModelGatewayPlanner,
    )

    requests = []

    class Gateway:
        async def generate_structured(self, request):
            requests.append(request)
            return SimpleNamespace(output=suggestion())

    async def redact(text):
        return text

    await ModelGatewayPlanner(Gateway(), scope=VALIDATION_SCOPE, redact=redact)(
        context(message), 0.5
    )
    schema = requests[0].schema
    assert requests[0].schema_digest == schema_digest(schema)
    if not constrained:
        assert schema == PLANNER_SCHEMA
        return
    queries = schema["properties"]["queries"]
    assert schema["properties"]["standalone_query"] == {"type": "string", "maxLength": 0}
    assert queries["maxItems"] == 1
    for field in ("text", "evidence_goal"):
        assert queries["items"]["properties"][field] == {"type": "string", "maxLength": 0}
    assert schema["required"] == PLANNER_SCHEMA["required"]
    assert PLANNER_SCHEMA["properties"]["standalone_query"] == {"type": "string"}
