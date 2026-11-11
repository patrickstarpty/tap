"""Closed public workflow HTTP and SSE contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from tap.modules.workflows.domain.acceptance_contract import BrowserAcceptanceContract
from tap.modules.workflows.domain.models import WorkflowNodeStatus, WorkflowStatus


class WorkflowContract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class WorkflowCreateRequest(WorkflowContract):
    goal: str = Field(min_length=1, max_length=16000)
    model_alias: str = Field(min_length=1, max_length=128)
    requested_agent_ids: list[str] = Field(default_factory=list, max_length=20)
    source_revision_ids: list[str] = Field(default_factory=list, max_length=20)
    document_revision_ids: list[str] = Field(default_factory=list, max_length=20)


class WorkflowCommandRequest(WorkflowContract):
    kind: Literal["steer", "pause", "resume", "cancel", "retry"]
    expected_plan_revision: int = Field(ge=1)
    target_node_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=16000)


class WorkflowCommandView(WorkflowContract):
    command_id: str
    kind: str
    target_node_id: str | None
    expected_plan_revision: int
    status: str
    reason: str | None = None
    applied_plan_revision: int | None = None


class WorkflowArtifactView(WorkflowContract):
    artifact_id: str
    node_id: str | None
    attempt: int
    content_digest: str
    version_ref: str


class WorkflowEdgeView(WorkflowContract):
    from_node_id: str
    to_node_id: str
    plan_revision: int


class WorkflowInputRef(WorkflowContract):
    source_id: str
    revision_id: str
    source_content_hash: str


class WorkflowAttemptView(WorkflowContract):
    attempt: int
    status: str
    created_at: datetime


class WorkflowNodeView(WorkflowContract):
    node_id: str
    parent_node_id: str | None
    agent_id: str | None
    agent_revision_id: str | None
    display_name: str
    kind: Literal["agent", "service", "approval"]
    status: WorkflowNodeStatus
    attempt: int
    plan_revision: int
    attempts: list[WorkflowAttemptView] = Field(default_factory=list)
    source_runtime: str
    public_summary: str | None = Field(default=None, max_length=400)
    input_refs: list[WorkflowInputRef] = Field(default_factory=list)


class WorkflowEventEnvelope(WorkflowContract):
    event_id: str
    task_id: str
    sequence: int = Field(ge=1)
    node_id: str | None
    attempt: int = Field(ge=0)
    kind: str
    payload: dict[str, object]
    occurred_at: datetime
    project_id: str | None = None
    source_runtime: str | None = None
    parent_node_id: str | None = None
    status: str | None = None


class WorkflowMatchingRun(WorkflowContract):
    node_id: str
    agent_id: str
    agent_revision_id: str | None
    attempt: int
    status: WorkflowNodeStatus
    created_at: datetime


class WorkflowRecentEvent(WorkflowContract):
    kind: str
    occurred_at: datetime


class WorkflowTaskView(WorkflowContract):
    task_id: str
    project_id: str
    conversation_id: str | None
    status: WorkflowStatus
    plan_revision: int
    last_sequence: int
    origin: Literal["native", "observed"]
    task_kind: Literal["native", "browser", "observed"] = "native"
    replay_of_task_id: str | None = None
    available_actions: list[Literal["steer", "pause", "resume", "cancel", "retry", "replay"]]
    display_name: str
    goal_summary: str = Field(max_length=200)
    created_at: datetime
    matching_runs: list[WorkflowMatchingRun] = Field(default_factory=list)
    recent_event: WorkflowRecentEvent | None = None


class WorkflowTaskPage(WorkflowContract):
    items: list[WorkflowTaskView]
    next_cursor: str | None = None


class WorkflowNodeDetail(WorkflowNodeView):
    artifacts: list[WorkflowArtifactView]


class WorkflowLogPage(WorkflowContract):
    items: list[WorkflowEventEnvelope]
    next_cursor: int | None = None


class WorkflowBrowserStep(WorkflowContract):
    title: str = Field(max_length=400)
    status: str = Field(max_length=32)
    duration_ms: int = Field(ge=0)


class WorkflowBrowserEvidence(WorkflowContract):
    evidence_id: str
    kind: Literal["screenshot", "trace", "result", "video"]
    sha256: str
    url: str


class WorkflowBrowserRun(WorkflowContract):
    execution_id: str
    bundle_id: str
    input_sha256: str
    status: Literal["passed", "failed", "canceled", "unknown"]
    failure_code: str | None = None
    replay_model_calls: int = Field(ge=0)
    steps: list[WorkflowBrowserStep]
    outputs: list[dict[str, object]]
    evidence: list[WorkflowBrowserEvidence]


class WorkflowBrowserIntent(WorkflowContract):
    acceptance_contract: BrowserAcceptanceContract | None = None
    goal: str
    start_url: str
    expected_result: str
    input_notes: str | None = None


class WorkflowBrowserInputRequest(WorkflowContract):
    question: str
    reason: Literal["missing_input", "external_block"]


class WorkflowTaskSnapshot(WorkflowTaskView):
    nodes: list[WorkflowNodeView]
    edges: list[WorkflowEdgeView]
    commands: list[WorkflowCommandView]
    artifacts: list[WorkflowArtifactView]
    events: list[WorkflowEventEnvelope]
    budget_limit: dict[str, int]
    budget_used: dict[str, int]
    browser_run: WorkflowBrowserRun | None = None
    browser_run_unavailable: bool = False
    browser_intent: WorkflowBrowserIntent | None = None
    browser_input_request: WorkflowBrowserInputRequest | None = None
