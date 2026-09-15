import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { CatalogWorkspace } from "./CatalogWorkspace";
import { PROTOTYPE_COPY } from "./copy";

afterEach(() => localStorage.clear());

it("restores the Agent creation entry and saves an exportable Markdown draft", async () => {
  const onUse = vi.fn();
  const props = {
    kind: "agent" as const,
    copy: PROTOTYPE_COPY.en,
    items: [
      {
        id: "approved-agent",
        kind: "agent" as const,
        origin: "built-in" as const,
        name: "Knowledge agent",
        description: "Answers questions",
        instructions: "Server-approved revision",
      },
    ],
    durableDrafts: true,
    projectId: "tapper-demo",
    onCreate: vi.fn(),
    onUpdate: vi.fn(),
    onUse,
  };
  const view = render(<CatalogWorkspace {...props} />);
  expect(
    screen.getByRole("button", { name: "Create agent draft" }),
  ).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Create agent draft" }),
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Name (kebab-case)" }),
    "claims-reviewer",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Description" }),
    "Review claims when approval rules change.",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Instructions" }),
    "# Claims reviewer\n\nCheck approval evidence.",
  );
  expect(screen.getByText(/name: claims-reviewer/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
  expect(
    screen.getByRole("listitem", { name: "claims-reviewer" }),
  ).toHaveTextContent("Local draft");
  expect(
    screen.getByRole("button", { name: "Download Markdown" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Use claims-reviewer in chat" }),
  ).not.toBeInTheDocument();
  expect(props.onCreate).not.toHaveBeenCalled();
  expect(onUse).not.toHaveBeenCalled();

  view.unmount();
  render(<CatalogWorkspace {...props} />);
  expect(
    screen.getByRole("listitem", { name: "claims-reviewer" }),
  ).toBeVisible();
  expect(localStorage.getItem("tap-md-drafts-v1:tapper-demo:agent")).toContain(
    "claims-reviewer",
  );
});
