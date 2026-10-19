from __future__ import annotations

from tap.modules.ai.domain.tool_calls import (
    AdmittedToolCall,
    RejectedToolCall,
    RequestedToolCall,
    admit_tool_calls,
    step_id,
)


def test_validate_tool_call_rejects_out_of_scope_sources() -> None:
    calls = [
        RequestedToolCall(
            call_id="c0",
            name="knowledge_search",
            arguments_json='{"query": "x", "sourceIds": ["src-x"]}',
        )
    ]
    result = admit_tool_calls(
        calls, visible=("knowledge.search",), selected_source_ids=frozenset({"src-a"})
    )
    assert result == (
        RejectedToolCall(call_id="c0", name="knowledge_search", reason="source-out-of-scope"),
    )


def test_validate_tool_call_rejects_invisible_unknown_and_malformed() -> None:
    calls = [
        RequestedToolCall(call_id="c0", name="graph_relations", arguments_json='{"query": "x"}'),
        RequestedToolCall(call_id="c1", name="delete_all", arguments_json="{}"),
        RequestedToolCall(call_id="c2", name="knowledge_search", arguments_json="{"),
        RequestedToolCall(call_id="c3", name="knowledge_search", arguments_json='{"query": ""}'),
        RequestedToolCall(
            call_id="c4",
            name="knowledge_search",
            arguments_json='{"query": "x", "extra": 1}',
        ),
    ]
    result = admit_tool_calls(calls, visible=("knowledge.search",), selected_source_ids=frozenset())
    assert result == (
        RejectedToolCall(call_id="c0", name="graph_relations", reason="tool-not-visible"),
        RejectedToolCall(call_id="c1", name="delete_all", reason="unknown-tool"),
        RejectedToolCall(call_id="c2", name="knowledge_search", reason="invalid-arguments"),
        RejectedToolCall(call_id="c3", name="knowledge_search", reason="invalid-arguments"),
        RejectedToolCall(call_id="c4", name="knowledge_search", reason="invalid-arguments"),
    )


def test_validate_tool_call_caps_calls_per_round() -> None:
    calls = [
        RequestedToolCall(call_id=f"c{i}", name="knowledge_search", arguments_json='{"query": "x"}')
        for i in range(4)
    ]
    result = admit_tool_calls(calls, visible=("knowledge.search",), selected_source_ids=frozenset())
    assert result[:3] == tuple(
        AdmittedToolCall(call_id=f"c{i}", tool_id="knowledge.search", arguments={"query": "x"})
        for i in range(3)
    )
    assert result[3] == RejectedToolCall(
        call_id="c3", name="knowledge_search", reason="too-many-calls"
    )


def test_step_id_format() -> None:
    assert step_id("t1", 2, 0) == "t1:r2:c0"


def test_insights_query_admits_partial_filters_and_rejects_unknown_filter_key() -> None:
    # I-2: `filters` sub-keys are optional, mirroring `StructuredInsightsTool.invoke`
    # (which reads each with `filters.get(key, ())`), not all-required.
    partial = RequestedToolCall(
        call_id="c0",
        name="insights_query",
        arguments_json=(
            '{"metricIds": ["first_pass_rate"], "filters": {"sourceIds": ["src-a"]}, '
            '"from": "2026-01-01", "to": "2026-02-01", "timezone": "UTC", '
            '"asOf": "2026-02-01T00:00:00Z"}'
        ),
    )
    result = admit_tool_calls(
        [partial], visible=("insights.query",), selected_source_ids=frozenset({"src-a"})
    )
    assert result == (
        AdmittedToolCall(
            call_id="c0",
            tool_id="insights.query",
            arguments={
                "metricIds": ["first_pass_rate"],
                "filters": {"sourceIds": ["src-a"]},
                "from": "2026-01-01",
                "to": "2026-02-01",
                "timezone": "UTC",
                "asOf": "2026-02-01T00:00:00Z",
            },
        ),
    )

    unknown_filter_key = RequestedToolCall(
        call_id="c1",
        name="insights_query",
        arguments_json=(
            '{"metricIds": ["first_pass_rate"], "filters": {"projects": ["p-1"]}, '
            '"from": "2026-01-01", "to": "2026-02-01", "timezone": "UTC", '
            '"asOf": "2026-02-01T00:00:00Z"}'
        ),
    )
    rejected = admit_tool_calls(
        [unknown_filter_key], visible=("insights.query",), selected_source_ids=frozenset()
    )
    assert rejected == (
        RejectedToolCall(call_id="c1", name="insights_query", reason="invalid-arguments"),
    )
