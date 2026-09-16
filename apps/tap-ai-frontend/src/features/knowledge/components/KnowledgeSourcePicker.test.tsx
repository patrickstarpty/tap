import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeSourcePicker } from "./KnowledgeSourcePicker";

const labels = {
  search: "Find sources",
  loading: "Loading sources",
  noReadySources: "No ready sources",
  noResults: "No matching sources",
  empty: "Upload your first source",
  pending: "Sources are processing",
  error: "Sources could not be loaded",
  retry: "Retry loading",
  ready: "Ready",
  selected: "selected",
  immutableRevision: "Immutable revision",
};
const sources = [
  { id: "src_a", name: "Policy", ready: true, pending: false },
  { id: "src_b", name: "Draft", ready: false, pending: true },
];

describe("KnowledgeSourcePicker", () => {
  it("keeps search focusable and toggles canonical ready sources using Space", async () => {
    function Harness() {
      const [selected, setSelected] = useState<string[]>([]);
      return (
        <KnowledgeSourcePicker
          labels={labels}
          sources={sources}
          loadState="loaded"
          selectedSourceIds={selected}
          onToggleSource={(id) =>
            setSelected(selected.includes(id) ? [] : [id])
          }
          onRetry={() => undefined}
        />
      );
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("textbox", { name: "Find sources" })).toHaveFocus();
    await user.tab();
    await user.keyboard(" ");
    expect(screen.getByRole("checkbox", { name: /Policy/ })).toBeChecked();
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
    expect(
      screen.queryByRole("checkbox", { name: /Draft/ }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "missing");
    expect(screen.getByText("No matching sources")).toBeInTheDocument();
  });
  it.each([
    [[], "loaded", "Upload your first source"],
    [[sources[1]!], "loaded", "Sources are processing"],
    [[{ ...sources[1]!, pending: false }], "loaded", "No ready sources"],
    [[], "loading", "Loading sources"],
  ] as const)("distinguishes library states %#", (items, loadState, text) => {
    render(
      <KnowledgeSourcePicker
        labels={labels}
        sources={items}
        loadState={loadState}
        selectedSourceIds={[]}
        onToggleSource={() => undefined}
        onRetry={() => undefined}
      />,
    );
    expect(screen.getByText(text)).toBeInTheDocument();
  });
  it("reports a load failure with a retry action rather than empty-library text", async () => {
    const retry = vi.fn();
    render(
      <KnowledgeSourcePicker
        labels={labels}
        sources={[]}
        loadState="error"
        selectedSourceIds={[]}
        onToggleSource={() => undefined}
        onRetry={retry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Sources could not be loaded",
    );
    expect(
      screen.queryByText("Upload your first source"),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Retry loading" }),
    );
    expect(retry).toHaveBeenCalledOnce();
  });
});
