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
      agentNames={{ a1: "Reviewer" }}
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
        agentNames={{}}
        copy={copy}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getByText(copy.agentTeam.team.orgChartEmpty)).toBeTruthy();
  });
});
