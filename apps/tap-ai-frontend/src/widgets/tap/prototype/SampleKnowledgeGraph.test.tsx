import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SampleKnowledgeGraph } from "./SampleKnowledgeGraph";
import { SAMPLE_SOURCES } from "./sampleKnowledge";

describe("Sample knowledge exploration", () => {
  it("searches business knowledge and follows its implementation without leaving the graph", async () => {
    const user = userEvent.setup();
    render(
      <SampleKnowledgeGraph
        chinese={false}
        query="beneficiary"
        sources={SAMPLE_SOURCES}
      />,
    );
    const results = screen.getByRole("region", { name: "Search results" });
    await user.click(
      within(results).getByRole("button", {
        name: "Change beneficiary",
      }),
    );
    const details = screen.getByRole("region", { name: "Node details" });
    expect(within(details).getByText("Demo model")).toBeVisible();
    await user.click(
      within(details).getByRole("button", { name: /beneficiary.ts/ }),
    );
    expect(within(details).getByText("Demo model")).toBeVisible();
    expect(
      within(details).getByRole("link", { name: "Download file" }),
    ).toHaveAttribute("download", "beneficiary.ts");
    await user.click(screen.getByRole("button", { name: "Focus connections" }));
    expect(screen.getByRole("button", { name: "Overview" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Overview" }));
    expect(
      screen.queryByRole("region", { name: "Node details" }),
    ).not.toBeInTheDocument();
  });
  it("browses a business group and opens its knowledge without a separate journey", async () => {
    const user = userEvent.setup();
    render(
      <SampleKnowledgeGraph
        chinese={false}
        query=""
        sources={SAMPLE_SOURCES}
      />,
    );
    await user.click(screen.getByRole("button", { name: /^Claims\s*\d+/ }));
    const group = screen.getByRole("region", { name: "Topic groups" });
    await user.click(
      within(group).getByRole("button", { name: "Medical claim documents" }),
    );
    expect(
      within(screen.getByRole("region", { name: "Node details" })).getByRole(
        "heading",
        { name: "Medical claim documents" },
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("group", { name: "Sample knowledge graph" }),
    ).toBeVisible();
    expect(screen.getByText(/illustrative, not executed/i)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Overview" }));
    expect(
      screen.getByRole("button", { name: /^New business\s*\d+/ }),
    ).toBeVisible();
  });
});
