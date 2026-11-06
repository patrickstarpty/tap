import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/app/styles.css";
import { WorkflowSidebar } from "../../src/features/workflows/components/WorkflowSidebar";
import { RunStatusLink } from "../../src/features/workflows/components/RunStatusLink";
import type { WorkflowTaskSnapshot } from "../../src/features/workflows/api/client";

const snapshot: WorkflowTaskSnapshot = {
  taskId: "task-capture",
  projectId: "project-capture",
  conversationId: "conversation-capture",
  status: "running",
  planRevision: 2,
  lastSequence: 4,
  origin: "native",
  availableActions: ["steer", "pause", "cancel"],
  displayName: "Tapper",
  goalSummary: "Review the login flow and prepare test coverage",
  createdAt: "2026-11-06T08:00:00Z",
  budgetLimit: {},
  budgetUsed: {},
  artifacts: [],
  commands: [],
  nodes: [
    {
      nodeId: "root",
      parentNodeId: null,
      agentId: "orchestrator",
      agentRevisionId: null,
      displayName: "Tapper",
      kind: "agent",
      status: "running",
      attempt: 1,
      planRevision: 2,
      sourceRuntime: "native",
      publicSummary: "Coordinating the plan and checking the results.",
    },
    {
      nodeId: "sdet",
      parentNodeId: "root",
      agentId: "sdet",
      agentRevisionId: "rev-sdet",
      displayName: "SDET",
      kind: "agent",
      status: "succeeded",
      attempt: 1,
      planRevision: 2,
      sourceRuntime: "native",
      publicSummary: "Mapped the main paths and edge cases.",
    },
    {
      nodeId: "reviewer",
      parentNodeId: "root",
      agentId: "reviewer",
      agentRevisionId: "rev-reviewer",
      displayName: "Reviewer",
      kind: "agent",
      status: "running",
      attempt: 1,
      planRevision: 2,
      sourceRuntime: "native",
      publicSummary: "Checking expected outcomes.",
    },
    {
      nodeId: "approval",
      parentNodeId: "root",
      agentId: null,
      agentRevisionId: null,
      displayName: "Approve coverage",
      kind: "approval",
      status: "waiting_dependency",
      attempt: 1,
      planRevision: 2,
      sourceRuntime: "native",
    },
  ],
  edges: [
    { fromNodeId: "sdet", toNodeId: "approval", planRevision: 2 },
    { fromNodeId: "reviewer", toNodeId: "approval", planRevision: 2 },
  ],
  events: [
    {
      eventId: "e-1",
      taskId: "task-capture",
      sequence: 1,
      nodeId: null,
      attempt: 1,
      kind: "task.created",
      payload: {},
      occurredAt: "2026-11-06T08:00:00Z",
      status: "queued",
    },
    {
      eventId: "e-2",
      taskId: "task-capture",
      sequence: 2,
      nodeId: "sdet",
      attempt: 1,
      kind: "node.finished",
      payload: { summary: "Mapped the main paths." },
      occurredAt: "2026-11-06T08:02:00Z",
      status: "succeeded",
    },
    {
      eventId: "e-3",
      taskId: "task-capture",
      sequence: 3,
      nodeId: "reviewer",
      attempt: 1,
      kind: "node.started",
      payload: {},
      occurredAt: "2026-11-06T08:03:00Z",
      status: "running",
    },
    {
      eventId: "e-4",
      taskId: "task-capture",
      sequence: 4,
      nodeId: "approval",
      attempt: 1,
      kind: "node.waiting",
      payload: { reason: "Waiting for both reviews" },
      occurredAt: "2026-11-06T08:04:00Z",
      status: "waiting_dependency",
    },
  ],
};

const queryClient = new QueryClient();

function App() {
  const [open, setOpen] = useState(false);
  const [nodeId, setNodeId] = useState<string | null>(null);
  return (
    <div className="workflow-fixture">
      <header className="workflow-fixture-head">
        <strong>Tapper</strong>
        <span>Knowledge Chat</span>
      </header>
      <main className="workflow-fixture-main">
        <section className="workflow-fixture-chat" aria-label="Conversation">
          <div className="workflow-fixture-bubble">
            Review the login flow and prepare test coverage.
          </div>
          <div className="workflow-fixture-answer">
            <strong>Tapper</strong>
            <p>I’m coordinating the review and will share the result here.</p>
            <RunStatusLink snapshot={snapshot} onOpen={() => setOpen(true)} />
          </div>
        </section>
        {open ? (
          <>
            <button
              className="workflow-fixture-scrim"
              aria-label="Close workflow overlay"
              onClick={() => setOpen(false)}
            />
            <div className="workflow-fixture-panel">
              <QueryClientProvider client={queryClient}>
                <WorkflowSidebar
                  projectId="project-capture"
                  snapshot={snapshot}
                  connection="live"
                  selectedNodeId={nodeId}
                  onSelectNode={setNodeId}
                  onClose={() => setOpen(false)}
                />
              </QueryClientProvider>
            </div>
          </>
        ) : null}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
