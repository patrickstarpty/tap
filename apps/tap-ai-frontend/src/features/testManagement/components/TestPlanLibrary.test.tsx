import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import {
  useRetryTestPlanGeneration,
  useTestPlanGeneration,
  useTestPlans,
} from "../api/queries";
import { TestPlanLibrary } from "./TestPlanLibrary";

vi.mock("../api/queries", () => ({
  useTestPlans: vi.fn(),
  useTestPlanGeneration: vi.fn(),
  useRetryTestPlanGeneration: vi.fn(),
}));

vi.mocked(useRetryTestPlanGeneration).mockReturnValue({
  mutate: vi.fn(),
  isPending: false,
} as never);

vi.mocked(useTestPlanGeneration).mockReturnValue({
  isError: false,
  data: undefined,
} as never);

it("shows service-generated plans with their review status and evidence", async () => {
  const onOpen = vi.fn();
  vi.mocked(useTestPlans).mockReturnValue({
    isPending: false,
    isError: false,
    data: [
      {
        testPlanId: "tp_underwriting",
        revisionId: "tpr_underwriting",
        title: "Fictional underwriting boundaries",
        objective: "Review age and amount rules against source evidence.",
        status: "DRAFT",
        origin: "VALIDATION",
        cases: [{}, {}],
        citations: [{}, {}, {}],
      },
    ],
  } as never);

  render(
    <TestPlanLibrary
      projectId="tapper-demo"
      locale="en"
      onOpen={onOpen}
      onGoTapper={vi.fn()}
    />,
  );

  const table = screen.getByRole("table", { name: "Test Plans" });
  const row = within(table).getByRole("row", {
    name: /Fictional underwriting boundaries/,
  });
  expect(within(row).getByText("2")).toBeVisible();
  expect(within(row).getByText("3")).toBeVisible();
  expect(within(row).getByText("Draft")).toBeVisible();
  expect(within(row).getByText("Validation")).toBeVisible();
  expect(
    within(row).getByText(
      "Review age and amount rules against source evidence.",
    ),
  ).toBeVisible();
  expect(within(row).queryByText("tp_underwriting")).not.toBeInTheDocument();

  await userEvent.click(within(row).getByRole("button", { name: "Open" }));
  expect(onOpen).toHaveBeenCalledWith("tp_underwriting", "tpr_underwriting");
});

it("offers a route to create a plan when the service has none", async () => {
  const onGoTapper = vi.fn();
  vi.mocked(useTestPlans).mockReturnValue({
    isPending: false,
    isError: false,
    data: [],
  } as never);

  render(
    <TestPlanLibrary
      projectId="tapper-demo"
      locale="en"
      onOpen={vi.fn()}
      onGoTapper={onGoTapper}
    />,
  );

  expect(screen.getByText("No Test Plans yet")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Go to Tapper" }));
  expect(onGoTapper).toHaveBeenCalledOnce();
});

it("shows generation progress instead of the empty state and reloads the list on completion", async () => {
  const refetch = vi.fn();
  vi.mocked(useTestPlans).mockReturnValue({
    isPending: false,
    isError: false,
    data: [],
    refetch,
  } as never);
  vi.mocked(useTestPlanGeneration).mockReturnValue({
    isError: false,
    data: { status: "RUNNING" },
  } as never);

  const props = {
    projectId: "tapper-demo",
    locale: "en" as const,
    generationJobId: "tpj_review",
    onOpen: vi.fn(),
    onGoTapper: vi.fn(),
  };
  const view = render(<TestPlanLibrary {...props} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Generating a Test Plan draft",
  );
  expect(screen.queryByText("No Test Plans yet")).not.toBeInTheDocument();
  expect(useTestPlanGeneration).toHaveBeenCalledWith(
    "tapper-demo",
    "tpj_review",
  );

  vi.mocked(useTestPlanGeneration).mockReturnValue({
    isError: false,
    data: {
      status: "DRAFT_READY",
      testPlanId: "tp_review",
      revisionId: "tpr_review",
    },
  } as never);
  view.rerender(<TestPlanLibrary {...props} />);
  await waitFor(() => expect(refetch).toHaveBeenCalledOnce());
  await userEvent.click(
    screen.getByRole("button", { name: "Open generated draft" }),
  );
  expect(props.onOpen).toHaveBeenCalledWith("tp_review", "tpr_review");
});

it("explains a failed generation and retries the same durable job", async () => {
  const retry = vi.fn();
  vi.mocked(useRetryTestPlanGeneration).mockReturnValue({
    mutate: retry,
    isPending: false,
  } as never);
  vi.mocked(useTestPlans).mockReturnValue({
    isPending: false,
    isError: false,
    data: [],
    refetch: vi.fn(),
  } as never);
  vi.mocked(useTestPlanGeneration).mockReturnValue({
    isError: false,
    data: { status: "FAILED" },
  } as never);
  render(
    <TestPlanLibrary
      projectId="tapper-demo"
      locale="en"
      generationJobId="tpj_failed"
      onOpen={vi.fn()}
      onGoTapper={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("could not be generated");
  await userEvent.click(
    screen.getByRole("button", { name: "Retry generation" }),
  );
  expect(retry).toHaveBeenCalledWith(
    expect.objectContaining({ jobId: "tpj_failed" }),
  );
});
