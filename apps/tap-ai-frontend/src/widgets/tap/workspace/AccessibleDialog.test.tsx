import { fireEvent, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { renderApp } from "../../../shared/testing/renderApp";
import { AccessibleDialog } from "./AccessibleDialog";

it("refocuses inside the dialog when its own content changes without remounting, so Escape still closes it", () => {
  // Regression for a multi-step dialog (e.g. SkillImportDialog's two-step
  // import) swapping its children in place: the old step's focused button
  // is removed from the new step, the browser drops focus to <body>, and
  // Escape (and Tab trapping) stopped working because that event target
  // falls outside the dialog's containment check.
  const close = vi.fn();
  function Wizard() {
    const [step, setStep] = useState<1 | 2>(1);
    return (
      <AccessibleDialog
        ariaLabel="Wizard"
        className="dialog"
        opener={null}
        onClose={close}
      >
        {step === 1 ? (
          <button onClick={() => setStep(2)}>Next</button>
        ) : (
          <button>Done</button>
        )}
      </AccessibleDialog>
    );
  }
  renderApp(<Wizard />);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  const stepTwoButton = screen.getByRole("button", { name: "Done" });
  expect(document.activeElement).toBe(stepTwoButton);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(close).toHaveBeenCalledTimes(1);
});

it("leaves keyboard events from a nested portaled editor to that editor", () => {
  const close = vi.fn();
  renderApp(
    <AccessibleDialog
      ariaLabel="Document"
      className="dialog"
      opener={null}
      onClose={close}
    >
      <button>Parent action</button>
      {createPortal(<button>Editor action</button>, document.body)}
    </AccessibleDialog>,
  );
  const editor = screen.getByRole("button", { name: "Editor action" });
  editor.focus();
  fireEvent.keyDown(editor, { key: "Tab" });
  expect(editor).toHaveFocus();
  fireEvent.keyDown(editor, { key: "Escape" });
  expect(close).not.toHaveBeenCalled();
});
