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

it("keeps focus in a nested portal (e.g. an antd Modal) across a parent re-render, instead of stealing it back (I-1)", () => {
  // Regression: `children` is a brand-new value on every parent render
  // (polling/streaming re-renders included), not just on step changes. The
  // refocus effect must not treat "active element is outside dialogRef" as
  // "focus was dropped" -- a nested portal the user is legitimately
  // interacting with (e.g. a confirm dialog rendered into document.body)
  // also looks that way, and previously lost focus on every re-render.
  const close = vi.fn();
  function Host() {
    const [tick, setTick] = useState(0);
    return (
      <div>
        <button onClick={() => setTick((value) => value + 1)}>
          Re-render {tick}
        </button>
        <AccessibleDialog
          ariaLabel="Host"
          className="dialog"
          opener={null}
          onClose={close}
        >
          <button>Parent action</button>
          {createPortal(<button>Portal action</button>, document.body)}
        </AccessibleDialog>
      </div>
    );
  }
  renderApp(<Host />);
  const portalButton = screen.getByRole("button", { name: "Portal action" });
  portalButton.focus();
  expect(document.activeElement).toBe(portalButton);

  fireEvent.click(screen.getByRole("button", { name: /Re-render/ }));

  expect(document.activeElement).toBe(portalButton);
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
