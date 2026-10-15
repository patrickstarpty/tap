import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "../../../shared/testing/renderApp";
import { AnswerFeedback } from "./AnswerFeedback";

afterEach(() => {
  vi.unstubAllGlobals();
});

const json = (value: unknown, status = 200) =>
  new Response(value === null ? null : JSON.stringify(value), {
    status,
    headers:
      value === null ? {} : { "content-type": "application/json" },
  });

function renderFeedback(
  overrides: Partial<Parameters<typeof AnswerFeedback>[0]> = {},
) {
  return renderApp(
    <AnswerFeedback
      projectId="project-1"
      conversationId="conversation-1"
      turnId="turn-1"
      locale="en"
      initialValue={null}
      {...overrides}
    />,
  );
}

describe("AnswerFeedback", () => {
  it("restores the selected state from the initial myFeedback", () => {
    renderFeedback({ initialValue: { rating: "down", comment: "thin" } });

    expect(
      screen.getByRole("button", { name: "Helpful" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("button", { name: "Not helpful" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("PUTs an up rating on click and highlights the button", async () => {
    const fetcher = vi.fn(async (request: Request) => {
      expect(request.method).toBe("PUT");
      expect(request.url).toContain(
        "/conversations/conversation-1/turns/turn-1/feedback",
      );
      expect(await request.json()).toEqual({ rating: "up", comment: null });
      return json({ rating: "up", comment: null });
    });
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Helpful" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Helpful" }),
      ).toHaveAttribute("aria-pressed", "true"),
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("PUTs a down rating immediately, then PUTs again with the submitted comment", async () => {
    const bodies: unknown[] = [];
    const fetcher = vi.fn(async (request: Request) => {
      bodies.push(await request.json());
      return json({ rating: "down", comment: null });
    });
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(bodies[0]).toEqual({ rating: "down", comment: null });

    const textbox = await screen.findByRole("textbox", {
      name: "Add a comment (optional)",
    });
    await user.type(textbox, "needs more detail");
    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(bodies[1]).toEqual({
      rating: "down",
      comment: "needs more detail",
    });
    expect(
      screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
    ).not.toBeInTheDocument();
  });

  it("DELETEs feedback when the already-selected button is clicked again", async () => {
    const fetcher = vi.fn(async (request: Request) => {
      expect(request.method).toBe("DELETE");
      expect(request.url).toContain(
        "/conversations/conversation-1/turns/turn-1/feedback",
      );
      return json(null, 204);
    });
    vi.stubGlobal("fetch", fetcher);
    renderFeedback({ initialValue: { rating: "up", comment: null } });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Helpful" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Helpful" }),
      ).toHaveAttribute("aria-pressed", "false"),
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reverts to the previous state and shows an error message when the request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ title: "Server error" }, 500)),
    );
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Helpful" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Helpful" }),
    ).toHaveAttribute("aria-pressed", "false");
  });
});
