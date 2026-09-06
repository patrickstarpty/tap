import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { LibraryWorkspace } from "./LibraryWorkspace";
import { PROTOTYPE_COPY } from "./copy";
import { SAMPLE_FILES } from "./sampleFiles";

describe("Library file browsing", () => {
  it("toggles all topics with a mixed-state checkbox", async () => {
    const user = userEvent.setup();
    render(
      <LibraryWorkspace
        copy={PROTOTYPE_COPY.en}
        sources={SAMPLE_FILES}
        onAddSource={() => {}}
      />,
    );
    const all = screen.getByRole("checkbox", { name: "Select all" });
    const topics = screen
      .getAllByRole("checkbox")
      .filter((topic) => topic !== all);
    expect(all).toBeChecked();
    await user.click(all);
    for (const topic of topics) expect(topic).not.toBeChecked();
    expect(all).not.toBePartiallyChecked();
    await user.click(all);
    for (const topic of topics) expect(topic).toBeChecked();
    await user.click(topics[0]!);
    expect(all).toBePartiallyChecked();
    await user.click(all);
    for (const topic of topics) expect(topic).toBeChecked();
    expect(all).not.toBePartiallyChecked();
    all.focus();
    await user.keyboard(" ");
    for (const topic of topics) expect(topic).not.toBeChecked();
    expect(
      screen.queryByRole("button", { name: "Invert selection" }),
    ).not.toBeInTheDocument();
  });
  it("clears search, type and status together from the adjacent button", async () => {
    const user = userEvent.setup();
    render(
      <LibraryWorkspace
        copy={PROTOTYPE_COPY.en}
        sources={SAMPLE_FILES}
        onAddSource={() => {}}
      />,
    );
    const search = screen.getByRole("textbox", { name: "Search library" });
    const clear = screen.getByRole("button", { name: "Clear filters" });
    expect(clear).toBeDisabled();
    expect(search.parentElement).toContainElement(clear);
    await user.type(search, "underwriting");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Type" }),
      "PDF",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "ready",
    );
    await user.click(clear);
    expect(search).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Type" })).toHaveValue("all");
    expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue("all");
    expect(clear).toBeDisabled();
  });
  it("keeps file clicks in place and offers direct downloads across views", async () => {
    const user = userEvent.setup();
    render(
      <LibraryWorkspace
        copy={PROTOTYPE_COPY.en}
        sources={SAMPLE_FILES}
        onAddSource={() => {}}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Documents" }));
    const sources = screen.getByRole("list", { name: "Library sources" });
    expect(within(sources).getAllByRole("listitem")).toHaveLength(18);
    await user.click(
      within(sources).getByText("Exploratory testing checklist.md"),
    );
    expect(
      screen.queryByRole("complementary", { name: "File preview" }),
    ).not.toBeInTheDocument();
    const download = screen.getByRole("link", {
      name: "Download file Exploratory testing checklist.md",
    });
    expect(download).toHaveAttribute(
      "href",
      "/prototype-files/exploratory-testing-checklist.md",
    );
    expect(download).toHaveAttribute("download");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Type" }),
      "PDF",
    );
    await user.click(screen.getByRole("button", { name: "Card view" }));
    expect(within(sources).getAllByRole("listitem")).toHaveLength(1);
    await user.click(within(sources).getByText("Underwriting test rules.pdf"));
    expect(
      screen.queryByRole("complementary", { name: "File preview" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "Download file Underwriting test rules.pdf",
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Examples loaded" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer a download when the source has no file URL", async () => {
    const user = userEvent.setup();
    render(
      <LibraryWorkspace
        copy={PROTOTYPE_COPY.en}
        onAddSource={() => {}}
        sources={[
          {
            id: "real",
            name: "Uploaded.pdf",
            type: "PDF",
            status: "ready",
            origin: "knowledge-base",
            description: "Knowledge source",
          },
        ]}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "Documents" }));
    await user.click(screen.getByText("Uploaded.pdf"));
    expect(
      screen.queryByRole("complementary", { name: "File preview" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Download file/ }),
    ).not.toBeInTheDocument();
  });
});
