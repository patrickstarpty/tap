import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "../../copy";
import { OrgChart } from "./OrgChart";

const copy = WORKSPACE_COPY.en;
const chart = {
  people: ["alice", "bob"],
  agents: ["a1", "tapper_default"],
  edges: [
    { from: "alice", to: "a1", kind: "owns" as const },
    { from: "bob", to: "a1", kind: "serves" as const },
    { from: "bob", to: "tapper_default", kind: "serves" as const },
  ],
};

function setup(onOpen = vi.fn()) {
  const view = render(
    <OrgChart
      chart={chart}
      agentStats={{ a1: { name: "Reviewer", runs: 1234, status: "active" } }}
      personRuns={{ alice: 7, bob: null }}
      copy={copy}
      onOpenMember={onOpen}
    />,
  );
  return { ...view, onOpen };
}

describe("OrgChart", () => {
  it("draws owns solid and serves dashed edges", () => {
    const { container } = setup();
    const owns = container.querySelectorAll('[data-edge-kind="owns"]');
    const serves = container.querySelectorAll('[data-edge-kind="serves"]');
    expect(owns).toHaveLength(1);
    expect(serves).toHaveLength(2);
    expect(owns[0]?.getAttribute("stroke-dasharray")).toBeNull();
    expect(serves[0]?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tapper default" })).toBeTruthy();
  });

  it("highlights a person's relations and toggles off", async () => {
    const { container } = setup();
    const dimmed = () =>
      [...container.querySelectorAll("line[data-edge-kind]")].map((l) =>
        l.getAttribute("data-dimmed"),
      );
    await userEvent.click(screen.getByRole("button", { name: "alice" }));
    expect(dimmed()).toEqual(["false", "true", "true"]);
    expect(
      screen.getByRole("button", { name: "bob" }).getAttribute("data-dimmed"),
    ).toBe("true");
    expect(
      screen
        .getByRole("button", { name: "alice" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "alice" }));
    expect(dimmed()).toEqual(["false", "false", "false"]);
  });

  it("tells assistive technology which nodes are dimmed", async () => {
    setup();
    const description = (name: string) =>
      screen.getByRole("button", { name }).getAttribute("aria-description");
    expect(description("bob")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "alice" }));
    const dimmed = copy.agentTeam.team.orgChartDimmed;
    expect(description("alice")).toBeNull();
    expect(description("bob")).toBe(dimmed);
    expect(description("Reviewer")).toBeNull();
    expect(description("Tapper default")).toBe(dimmed);
  });

  it("opens an agent from click and keyboard", async () => {
    const { onOpen } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Reviewer" }));
    expect(onOpen).toHaveBeenCalledWith("a1");
    screen.getByRole("button", { name: "Reviewer" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("shows an empty state without edges", () => {
    render(
      <OrgChart
        chart={{ people: [], agents: [], edges: [] }}
        agentStats={{}}
        copy={copy}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getByText(copy.agentTeam.team.orgChartEmpty)).toBeTruthy();
  });

  it("shows run counts and a status dot on each chip", () => {
    const { container } = setup();
    const reviewer = screen.getByRole("button", { name: "Reviewer" });
    expect(reviewer).toHaveTextContent("1.2K");
    expect(
      reviewer.querySelector(".tap-org-status")?.getAttribute("data-status"),
    ).toBe("active");
    expect(screen.getByRole("button", { name: "alice" })).toHaveTextContent(
      "7",
    );
    // Unknown counts are left out rather than shown as zero.
    expect(
      screen
        .getByRole("button", { name: "bob" })
        .querySelector(".tap-org-count"),
    ).toBeNull();
    expect(container.querySelectorAll(".tap-org-count")).toHaveLength(2);
  });
});
