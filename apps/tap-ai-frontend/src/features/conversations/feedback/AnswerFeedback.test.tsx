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
    headers: value === null ? {} : { "content-type": "application/json" },
  });

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

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

    expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
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
      expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
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
      expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
        "aria-pressed",
        "false",
      ),
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

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("disables the rating buttons while a request is in flight and ignores a second click", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn(async () => pending.promise);
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Helpful" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Helpful" })).toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "Not helpful" })).toBeDisabled();

    // A second click while the first request is still pending must not
    // fire another request — TanStack Query v5 only tracks the latest
    // mutate() call's onError, so an unguarded second click would race.
    await user.click(screen.getByRole("button", { name: "Helpful" }));
    expect(fetcher).toHaveBeenCalledTimes(1);

    pending.resolve(json({ rating: "up", comment: null }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Helpful" }),
      ).not.toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "Helpful" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("restores the rating and closes the comment box when a not-helpful request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ title: "Server error" }, 500)),
    );
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(
      screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the typed comment and the comment box open when Submit fails, reverting only the rating", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      if (call === 1) return json({ rating: "down", comment: null });
      return json({ title: "Server error" }, 500);
    });
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    const textbox = await screen.findByRole("textbox", {
      name: "Add a comment (optional)",
    });
    await user.type(textbox, "needs more detail");
    await user.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(
      await screen.findByRole("textbox", {
        name: "Add a comment (optional)",
      }),
    ).toHaveValue("needs more detail");
    expect(screen.getByRole("button", { name: "Not helpful" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("sends no request on Skip and closes the comment box", async () => {
    const fetcher = vi.fn(async () => json({ rating: "down", comment: null }));
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    await screen.findByRole("textbox", { name: "Add a comment (optional)" });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
    ).not.toBeInTheDocument();
  });

  it("returns focus to the not-helpful button after a successful Submit, even though it was disabled mid-request", async () => {
    const submitResponse = deferred<Response>();
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      if (call === 1) return json({ rating: "down", comment: null });
      return submitResponse.promise;
    });
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    const textbox = await screen.findByRole("textbox", {
      name: "Add a comment (optional)",
    });
    await user.type(textbox, "needs more detail");
    await user.click(screen.getByRole("button", { name: "Submit" }));

    // While the Submit PUT is in flight, 👎 is disabled (the busy guard
    // from fix round 1) — exercising the exact window in which calling
    // .focus() on it is a silent no-op.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Not helpful" }),
      ).toBeDisabled(),
    );
    submitResponse.resolve(
      json({ rating: "down", comment: "needs more detail" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
      ).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Not helpful" })).toHaveFocus(),
    );
  });

  it("returns focus to the not-helpful button after Skip", async () => {
    const fetcher = vi.fn(async () => json({ rating: "down", comment: null }));
    vi.stubGlobal("fetch", fetcher);
    renderFeedback();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    await screen.findByRole("textbox", { name: "Add a comment (optional)" });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "Skip" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Not helpful" })).toHaveFocus(),
    );
  });

  it("shows the Chinese error message when a request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ title: "Server error" }, 500)),
    );
    renderFeedback({ locale: "zh" });
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "有帮助" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "反馈保存失败，请重试。",
    );
  });
});
