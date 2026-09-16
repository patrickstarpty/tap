import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { App } from "./App";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("TAP non-AI application", () => {
  it("restores a created automation, edited BDD step and simulated run after remount", async () => {
    const user = userEvent.setup();
    const mounted = render(<App />);
    await user.click(screen.getByRole("button", { name: "New automation" }));
    await user.type(
      screen.getByRole("textbox", { name: "Automation title" }),
      "Persistent browser journey",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Automation type" }),
      "web",
    );
    await user.click(
      screen.getByRole("button", { name: "Create blank automation" }),
    );
    const step = screen.getByRole("textbox", { name: "BDD step text 1" });
    await user.clear(step);
    await user.type(step, "the saved customer opens the application");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Execution Agent" }),
      "ado-web-agent-03",
    );
    await user.click(screen.getByRole("button", { name: "Run automation" }));
    expect(
      within(
        screen.getByRole("region", { name: "Automation run history" }),
      ).queryByText("No simulated runs yet"),
    ).not.toBeInTheDocument();
    mounted.unmount();
    render(<App />);
    await user.click(screen.getByText("Persistent browser journey"));
    expect(
      screen.getByRole("textbox", { name: "BDD step text 1" }),
    ).toHaveValue("the saved customer opens the application");
    expect(
      within(
        screen.getByRole("region", { name: "Automation run history" }),
      ).queryByText("No simulated runs yet"),
    ).not.toBeInTheDocument();
  });

  it("opens automation and analytics without mounting Tapper", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(
      screen.getByRole("button", { name: "Low Code Automation" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Tapper" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test Analytics" }));
    expect(
      screen.getByRole("heading", { name: "Demo Dashboard" }),
    ).toBeVisible();
  });

  it("keeps the automation editor free of the Tapper agent panel", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText("Life insurance application automation"));
    expect(
      screen.queryByRole("tab", { name: "AI Agent" }),
    ).not.toBeInTheDocument();
  });
});
