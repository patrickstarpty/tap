import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { BddResults } from "./BddResults";
import type { LcaRun } from "./client";
it("uses frozen BDD descriptions and exposes failed action identity", () => {
  const run = {
    canonical_results: [{ step_id: "click", status: "failed" }],
    definition_snapshot: {
      definition: {
        bdd_steps: [
          {
            id: "choose",
            keyword: "When",
            description: "Choose saved product",
            action_ids: ["click"],
          },
        ],
        steps: [
          { id: "click", operation: "click", arguments: {}, timeout_ms: 1000 },
        ],
        targets: [],
      },
    },
  } as unknown as LcaRun;
  render(<BddResults run={run} />);
  expect(screen.getByText(/When Choose saved product/)).toBeVisible();
  expect(screen.getByText(/click · failed/)).toBeInTheDocument();
});
