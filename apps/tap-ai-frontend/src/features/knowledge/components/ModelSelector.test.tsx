import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchModelCatalog } from "../api/modelCatalog";
import { ModelSelector } from "./ModelSelector";

afterEach(() => vi.restoreAllMocks());

it("loads only the Project catalog without provider or reasoning fields", async () => {
  const catalog = {
    defaultAlias: "tapper-chat",
    items: [
      {
        alias: "tapper-chat",
        displayName: "GPT-5.6 Sol",
        capabilities: ["chat"],
      },
    ],
  };
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(catalog)));
  expect(await fetchModelCatalog("project/one")).toEqual(catalog);
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/projects/project%2Fone/ai/models",
  );
});

it.each([
  { defaultAlias: "missing", items: [] },
  { defaultAlias: "tapper-chat", items: [null] },
  { defaultAlias: "tapper-chat", items: [], provider: "private-provider" },
  {
    defaultAlias: "tapper-chat",
    items: [
      {
        alias: "tapper-chat",
        displayName: "Sol",
        capabilities: ["chat"],
        reasoning: "private",
      },
    ],
  },
])(
  "rejects malformed or private catalog data without a fallback",
  async (catalog) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(catalog)),
    );
    await expect(fetchModelCatalog("project")).rejects.toThrow(
      "Model catalog is unavailable.",
    );
  },
);

describe("ModelSelector", () => {
  it("moves with arrows and Home/End, selects with Enter, and dismisses with Escape", async () => {
    function Harness() {
      const [value, onChange] = useState("first");
      return (
        <ModelSelector
          models={[
            {
              alias: "first",
              displayName: "GPT-5.6 Sol",
              capabilities: ["chat"],
            },
            {
              alias: "second",
              displayName: "Approved alternate",
              capabilities: ["chat"],
            },
          ]}
          value={value}
          onChange={onChange}
        />
      );
    }
    render(<Harness />);
    expect(screen.getByRole("button", { name: "GPT-5.6 Sol" })).toHaveClass(
      "tap-model-trigger",
    );
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard("{ArrowDown}{End}");
    expect(
      screen.getByRole("menuitemradio", { name: "Approved alternate" }),
    ).toHaveFocus();
    await user.keyboard("{Home}");
    expect(
      screen.getByRole("menuitemradio", { name: "GPT-5.6 Sol" }),
    ).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approved alternate" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("fails closed when the selected alias disappears", () => {
    render(
      <ModelSelector
        models={[
          { alias: "allowed", displayName: "Approved", capabilities: ["chat"] },
        ]}
        value="disabled"
        onChange={() => {}}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Approved" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Model unavailable");
  });

  it("opens an accessible keyboard menu of server-governed model aliases", async () => {
    function Harness() {
      const [alias, setAlias] = useState("tapper-chat");
      return (
        <ModelSelector
          models={[
            {
              alias: "tapper-chat",
              displayName: "GPT-5.6 Sol",
              capabilities: ["chat"],
            },
          ]}
          value={alias}
          onChange={setAlias}
        />
      );
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.tab();
    await user.keyboard(" ");
    expect(
      screen.getByRole("menuitemradio", { name: "GPT-5.6 Sol" }),
    ).toHaveAttribute("aria-checked", "true");
  });
});
