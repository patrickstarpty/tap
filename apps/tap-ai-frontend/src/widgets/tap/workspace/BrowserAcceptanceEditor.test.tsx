import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { BrowserAcceptanceEditor } from "./BrowserAcceptanceEditor";
import {
  browserAcceptanceReady,
  type BrowserAcceptanceDraft,
} from "../../../features/workflows/model/browserInput";

it("requires explicit values and re-review after every edit", async () => {
  function Composer() {
    const [draft, setDraft] = useState<BrowserAcceptanceDraft>({
      reviewed: false,
      fields: [],
    });
    return (
      <>
        <BrowserAcceptanceEditor draft={draft} onChange={setDraft} />
        <button disabled={!browserAcceptanceReady(draft)}>Send test</button>
      </>
    );
  }
  render(<Composer />);
  expect(
    screen.getByText(/freeform input details are not independently/),
  ).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Add required input" }),
  );
  const send = screen.getByRole("button", { name: "Send test" });
  expect(send).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Field ID"), "start_date");
  await userEvent.type(screen.getByLabelText("Input value"), "2026-11-11");
  await userEvent.type(
    screen.getByLabelText("Expected readback value"),
    "11/11/2026",
  );
  await userEvent.type(
    screen.getByLabelText("Exact business labels (one per line)"),
    "Start date",
  );
  const review = screen.getByRole("checkbox");
  expect(send).toBeDisabled();
  await userEvent.click(review);
  expect(send).toBeEnabled();
  await userEvent.type(
    screen.getByLabelText("Scope text (optional)"),
    "Booking",
  );
  expect(review).not.toBeChecked();
  expect(send).toBeDisabled();
  await userEvent.click(review);
  await userEvent.selectOptions(
    screen.getByLabelText("Readback attribute"),
    "text",
  );
  expect(review).not.toBeChecked();
  await userEvent.click(review);
  await userEvent.type(screen.getByLabelText("Input value"), "x");
  expect(review).not.toBeChecked();
});

it.each(["data-active", "selected_label"])(
  "offers %s only with explicit review",
  async (attribute) => {
    function Composer() {
      const [draft, setDraft] = useState<BrowserAcceptanceDraft>({
        reviewed: false,
        fields: [
          {
            id: "choice",
            value: "Option A",
            readbackValue: "",
            attribute: "value",
            targetTexts: ["Option A"],
          },
        ],
      });
      return (
        <>
          <BrowserAcceptanceEditor draft={draft} onChange={setDraft} />
          <button disabled={!browserAcceptanceReady(draft)}>Send state</button>
        </>
      );
    }
    render(<Composer />);
    await userEvent.selectOptions(
      screen.getByLabelText("Readback attribute"),
      attribute,
    );
    expect(screen.getByLabelText("Expected readback value")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Send state" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Send state" })).toBeEnabled();
  },
);
