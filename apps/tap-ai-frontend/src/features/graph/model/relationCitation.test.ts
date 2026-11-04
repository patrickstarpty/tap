import { afterEach, expect, it } from "vitest";
import { relationGraphSelection, relationGraphUrl } from "./relationCitation";

afterEach(() => window.history.replaceState(null, "", "/"));
it("preserves the project route and encodes a citation's immutable snapshot and complete path", () => {
  window.history.replaceState(
    null,
    "",
    "/projects/project-a?conversation=chat-a",
  );
  const href = relationGraphUrl({
    citationId: "r",
    snapshotId: "snapshot/old",
    edgeId: "e1",
    pathEdgeIds: ["e1", "e2"],
    sourceNodeId: "a",
    targetNodeId: "b",
    sourceLabel: "A",
    targetLabel: "B",
    relationType: "RELATES",
    origin: "EXTRACTED",
    claimIndex: 0,
  });
  window.history.replaceState(null, "", href);
  expect(window.location.pathname).toBe("/projects/project-a");
  expect(new URLSearchParams(window.location.search).get("module")).toBe(
    "library",
  );
  expect(relationGraphSelection()).toEqual({
    snapshotId: "snapshot/old",
    edgeIds: ["e1", "e2"],
  });
});
