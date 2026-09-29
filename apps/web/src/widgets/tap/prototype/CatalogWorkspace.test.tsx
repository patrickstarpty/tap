import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CatalogWorkspace } from "./CatalogWorkspace";
import { PROTOTYPE_COPY } from "./copy";
import type { CatalogItem } from "./model";

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const copy = PROTOTYPE_COPY.en;

const items: readonly CatalogItem[] = [
  {
    id: "bdd-scenario-design",
    kind: "skill",
    origin: "built-in",
    name: "BDD Scenario Design",
    description: "Turns underwriting rules into focused BDD scenarios.",
    instructions: "Create concise Given, When, Then scenarios.",
  },
  {
    id: "custom-review-helper",
    kind: "skill",
    origin: "custom",
    name: "custom-review-helper",
    description: "Custom description.",
    instructions: "Custom instructions.",
  },
];

it("keeps built-in items read-only", () => {
  render(
    <CatalogWorkspace
      copy={copy}
      items={items}
      kind="skill"
      onCreate={vi.fn()}
      onUpdate={vi.fn()}
      onUse={vi.fn()}
    />,
  );

  const builtInRow = screen.getByRole("listitem", {
    name: "BDD Scenario Design",
  });
  expect(
    within(builtInRow).queryByRole("button", { name: /edit/i }),
  ).not.toBeInTheDocument();
  expect(
    within(builtInRow).getByRole("button", { name: /use/i }),
  ).toBeInTheDocument();

  const customRow = screen.getByRole("listitem", {
    name: "custom-review-helper",
  });
  expect(
    within(customRow).getByRole("button", { name: /edit/i }),
  ).toBeInTheDocument();
});

it("validates the name and previews SKILL.md while creating", () => {
  render(
    <CatalogWorkspace
      copy={copy}
      items={items}
      kind="skill"
      onCreate={vi.fn()}
      onUpdate={vi.fn()}
      onUse={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: copy.catalog.createSkill }));

  const nameInput = screen.getByLabelText(copy.catalog.name);
  const saveButton = screen.getByRole("button", {
    name: copy.catalog.saveSkill,
  });

  expect(screen.getByText(copy.catalog.nameRule)).toBeInTheDocument();

  fireEvent.change(nameInput, { target: { value: "Bad Name" } });
  expect(nameInput).toHaveAttribute("aria-invalid", "true");
  expect(saveButton).toBeDisabled();

  fireEvent.change(nameInput, { target: { value: "bdd-writer" } });
  expect(nameInput).not.toHaveAttribute("aria-invalid", "true");
  expect(saveButton).not.toBeDisabled();

  const preview = screen.getByLabelText("SKILL.md preview");
  expect(preview).toHaveTextContent("name: bdd-writer");
});
