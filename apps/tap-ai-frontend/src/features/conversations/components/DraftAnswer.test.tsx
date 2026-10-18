import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DraftAnswer } from "./DraftAnswer";

describe("DraftAnswer", () => {
  it("renders a known inline label as a badge carrying its source title", () => {
    render(
      <DraftAnswer
        draft={{ attempt: 1, seq: 1, text: "A rule applies [S3]." }}
        citationLabels={{ S3: { title: "Policy handbook", location: "p. 4" } }}
        stage={null}
        partialAnswer={null}
        canceled={false}
      />,
    );

    const badge = screen.getByText("[S3]");
    expect(badge).toBeVisible();
    expect(badge.getAttribute("title")).toContain("Policy handbook");
  });

  it("renders an unknown inline label as plain text", () => {
    render(
      <DraftAnswer
        draft={{ attempt: 1, seq: 1, text: "A rule applies [S9]." }}
        citationLabels={{}}
        stage={null}
        partialAnswer={null}
        canceled={false}
      />,
    );

    expect(screen.getByText(/A rule applies \[S9\]\./u)).toBeVisible();
    expect(screen.queryByTitle(/./u)).toBeNull();
  });

  it("shows the checking-sources notice while the verify stage runs", () => {
    render(
      <DraftAnswer
        draft={{ attempt: 1, seq: 1, text: "Draft text." }}
        citationLabels={{}}
        stage="verify"
        partialAnswer={null}
        canceled={false}
        locale="zh"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在核对依据");
  });

  it("shows the checking-sources notice in English", () => {
    render(
      <DraftAnswer
        draft={{ attempt: 1, seq: 1, text: "Draft text." }}
        citationLabels={{}}
        stage="verify"
        partialAnswer={null}
        canceled={false}
        locale="en"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Checking sources");
  });

  it("shows the partial answer and the unverified stopped notice after cancellation", () => {
    render(
      <DraftAnswer
        draft={null}
        citationLabels={{}}
        stage={null}
        partialAnswer="What we had so far."
        canceled
        locale="zh"
      />,
    );

    expect(screen.getByText("What we had so far.")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("已停止 · 未核对依据");
  });

  it("never renders a citation badge in the partial answer, even for a known label", () => {
    render(
      <DraftAnswer
        draft={null}
        citationLabels={{ S3: { title: "Policy handbook", location: "p. 4" } }}
        stage={null}
        partialAnswer="A rule applies [S3]."
        canceled
        locale="zh"
      />,
    );

    expect(screen.getByText(/A rule applies \[S3\]\./u)).toBeVisible();
    expect(screen.queryByTitle(/./u)).toBeNull();
  });

  it("shows only the stopped notice when canceled before any draft text", () => {
    render(
      <DraftAnswer
        draft={null}
        citationLabels={{}}
        stage={null}
        partialAnswer={null}
        canceled
        locale="en"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Stopped · not verified",
    );
    expect(document.querySelector(".tapper-markdown")).toBeNull();
  });
});
