import { expect, it } from "vitest";
import type { components } from "../../../shared/api/generated/schema";
import { layoutWorkflow } from "./layout";

type Node = components["schemas"]["WorkflowNodeView"];
type Edge = components["schemas"]["WorkflowEdgeView"];
const node = (nodeId: string, parentNodeId: string | null = null): Node => ({
  nodeId,
  parentNodeId,
  agentId: nodeId,
  agentRevisionId: `${nodeId}-v1`,
  displayName: nodeId,
  kind: "agent",
  status: "queued",
  attempt: 1,
  planRevision: 1,
  sourceRuntime: "native",
});
const edge = (fromNodeId: string, toNodeId: string): Edge => ({
  fromNodeId,
  toNodeId,
  planRevision: 1,
});

it("places independent work in parallel and dependent work after it", () => {
  expect(
    layoutWorkflow(
      [node("b"), node("a"), node("c")],
      [edge("a", "c"), edge("b", "c")],
    ),
  ).toEqual({ layers: [["a", "b"], ["c"]], error: null });
});

it("hides descendants of a collapsed parent", () => {
  expect(
    layoutWorkflow(
      [node("root"), node("child", "root")],
      [],
      new Set(["root"]),
    ),
  ).toEqual({ layers: [["root"]], error: null });
});

it("reports cycles instead of drawing a misleading execution order", () => {
  expect(
    layoutWorkflow([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])
      .error,
  ).toBe("cycle");
});
