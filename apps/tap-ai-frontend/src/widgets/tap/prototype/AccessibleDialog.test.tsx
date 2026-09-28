import { fireEvent, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { expect, it, vi } from "vitest";
import { renderApp } from "../../../shared/testing/renderApp";
import { AccessibleDialog } from "./AccessibleDialog";
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
