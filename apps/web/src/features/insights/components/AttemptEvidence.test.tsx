import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { InsightsDataAdapter } from "../api/client";
import { AttemptEvidence } from "./AttemptEvidence";

it("loads authorized steps, failure text and attachment controls for the exact attempt", async () => {
  const adapter = {
    getReportEvidence: vi.fn().mockResolvedValue({
      reportFormat: "allure",
      attempts: [
        {
          factKey: "other",
          name: "Other test",
          status: "passed",
          message: "",
          trace: "",
          steps: [],
          attachments: [],
        },
        {
          factKey: "fact-1",
          name: "Payment",
          status: "failed",
          message: "Rejected",
          trace: "assert accepted",
          attachments: [],
          steps: [
            {
              name: "Submit",
              status: "failed",
              message: "",
              trace: "",
              steps: [],
              attachments: [
                {
                  name: "Screenshot",
                  source: "screen.png",
                  mediaType: "image/png",
                  available: true,
                },
              ],
            },
          ],
        },
      ],
    }),
  } as unknown as InsightsDataAdapter;
  render(
    <AttemptEvidence
      adapter={adapter}
      projectId="project"
      receiptId="receipt"
      factKey="fact-1"
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "View report evidence" }));
  expect(await screen.findByText("Rejected")).toBeVisible();
  expect(screen.getByText("Submit")).toBeVisible();
  expect(screen.queryByText("Other test")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open Screenshot" })).toBeVisible();
  expect(adapter.getReportEvidence).toHaveBeenCalledWith("project", "receipt");
});

it("matches attempts stored under the frozen legacy fact key", async () => {
  const adapter = {
    getReportEvidence: vi.fn().mockResolvedValue({
      reportFormat: "junit",
      attempts: [
        {
          factKey: "current-key",
          legacyFactKey: "legacy-key",
          name: "Checkout",
          status: "failed",
          message: "Declined",
          trace: "",
          steps: [],
          attachments: [],
        },
      ],
    }),
  } as unknown as InsightsDataAdapter;
  render(
    <AttemptEvidence
      adapter={adapter}
      projectId="project"
      receiptId="receipt"
      factKey="legacy-key"
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "View report evidence" }));
  expect(await screen.findByText("Declined")).toBeVisible();
});
