import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { App } from "./App";
beforeEach(() => localStorage.clear());
afterEach(cleanup);

it("opens Low Code Automation as an asset library", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "Low Code Automation" }));

  const table = screen.getByRole("table", { name: "Low Code Automation" });
  expect(within(table).getByRole("row", { name: /AUTO-101/ })).toBeVisible();
  expect(within(table).getByRole("row", { name: /AUTO-102/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /New automation/ })).toBeVisible();

  await user.click(screen.getByRole("row", { name: /AUTO-101/ }));
  expect(
    screen.getByRole("heading", {
      name: "Life insurance application automation",
    }),
  ).toBeVisible();
});

it("asks for an explicit channel when generation intent is ambiguous", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "Low Code Automation" }));
  await user.click(screen.getByRole("button", { name: "New automation" }));
  await user.type(
    screen.getByRole("textbox", { name: "Automation title" }),
    "Cross-channel onboarding",
  );
  await user.type(
    screen.getByRole("textbox", { name: "Describe what to automate" }),
    "Run onboarding in a browser and Android app",
  );
  await user.click(screen.getByRole("button", { name: "Generate BDD" }));

  expect(screen.getByRole("alert")).toHaveTextContent(
    "This could be Web and Mobile. Choose a type to continue.",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Automation type" }),
    "web",
  );
  await user.click(screen.getByRole("button", { name: "Generate BDD" }));
  expect(
    screen.getByRole("heading", { name: "Cross-channel onboarding" }),
  ).toBeVisible();
});

it("creates a manual Automation with an editable starter BDD step", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "Low Code Automation" }));
  await user.click(screen.getByRole("button", { name: "New automation" }));
  await user.type(
    screen.getByRole("textbox", { name: "Automation title" }),
    "Manual quote review",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Automation type" }),
    "web",
  );
  await user.click(
    screen.getByRole("button", { name: "Create blank automation" }),
  );

  expect(
    screen.getByRole("heading", { name: "Manual quote review" }),
  ).toBeVisible();
  expect(screen.getByRole("textbox", { name: "BDD step text 1" })).toHaveValue(
    "Describe the starting context",
  );
});

it("gates Mobile execution on a supported platform and available device", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.click(screen.getByRole("button", { name: "Low Code Automation" }));
  await user.click(screen.getByRole("row", { name: /AUTO-102/ }));
  const run = screen.getByRole("button", { name: "Run automation" });
  expect(run).toBeDisabled();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Run platform" }),
    "ios",
  );
  expect(run).toBeDisabled();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Device" }),
    "iphone-15",
  );
  expect(run).toBeEnabled();
  await user.click(run);
  expect(screen.getByText("Completed · Simulated")).toBeVisible();
});
