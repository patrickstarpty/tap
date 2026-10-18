import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useCreateAgent,
  useCreateSkill,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { LocalDraftMigrationBanner } from "./LocalDraftMigrationBanner";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useCreateAgent: vi.fn(),
      useCreateSkill: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

afterEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

const AGENT_DRAFT = {
  id: "local-agent-draft-1",
  kind: "agent" as const,
  origin: "custom" as const,
  name: "claims-reviewer",
  description: "Review claims when approval rules change.",
  instructions: "# Claims reviewer\n\nCheck approval evidence.",
};

describe("LocalDraftMigrationBanner", () => {
  it("renders nothing when there is no local draft", () => {
    const createAgent = vi.fn();
    vi.mocked(useCreateAgent).mockReturnValue({
      mutateAsync: createAgent,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgent>);
    vi.mocked(useCreateSkill).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateSkill>);
    const { container } = render(
      <LocalDraftMigrationBanner
        projectId="tapper-demo"
        kind="agent"
        copy={WORKSPACE_COPY.en}
      />,
      { wrapper },
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("imports a local agent draft and clears it from localStorage", async () => {
    localStorage.setItem(
      "tap-md-drafts-v1:tapper-demo:agent",
      JSON.stringify([AGENT_DRAFT]),
    );
    const createAgent = vi.fn().mockResolvedValue({ revisionId: "rev-1" });
    vi.mocked(useCreateAgent).mockReturnValue({
      mutateAsync: createAgent,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgent>);
    vi.mocked(useCreateSkill).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateSkill>);

    const { container } = render(
      <LocalDraftMigrationBanner
        projectId="tapper-demo"
        kind="agent"
        copy={WORKSPACE_COPY.en}
      />,
      { wrapper },
    );
    const item = screen.getByRole("listitem");
    expect(within(item).getByText("claims-reviewer")).toBeVisible();

    const user = userEvent.setup();
    await user.click(within(item).getByRole("button", { name: "Import" }));

    expect(createAgent).toHaveBeenCalledWith({
      name: "claims-reviewer",
      description: "Review claims when approval rules change.",
      systemInstruction: "# Claims reviewer\n\nCheck approval evidence.",
    });
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(
      localStorage.getItem("tap-md-drafts-v1:tapper-demo:agent"),
    ).toBeNull();
  });

  it("dismisses a draft without importing it", async () => {
    localStorage.setItem(
      "tap-md-drafts-v1:tapper-demo:agent",
      JSON.stringify([AGENT_DRAFT]),
    );
    const createAgent = vi.fn();
    vi.mocked(useCreateAgent).mockReturnValue({
      mutateAsync: createAgent,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgent>);
    vi.mocked(useCreateSkill).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateSkill>);

    const { container } = render(
      <LocalDraftMigrationBanner
        projectId="tapper-demo"
        kind="agent"
        copy={WORKSPACE_COPY.en}
      />,
      { wrapper },
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(createAgent).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
    expect(
      localStorage.getItem("tap-md-drafts-v1:tapper-demo:agent"),
    ).toBeNull();
  });

  it("shows an error and keeps the draft when import fails", async () => {
    localStorage.setItem(
      "tap-md-drafts-v1:tapper-demo:agent",
      JSON.stringify([AGENT_DRAFT]),
    );
    const createAgent = vi.fn().mockRejectedValue(new Error("boom"));
    vi.mocked(useCreateAgent).mockReturnValue({
      mutateAsync: createAgent,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgent>);
    vi.mocked(useCreateSkill).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useCreateSkill>);

    render(
      <LocalDraftMigrationBanner
        projectId="tapper-demo"
        kind="agent"
        copy={WORKSPACE_COPY.en}
      />,
      { wrapper },
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Import" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This draft could not be imported.",
    );
    expect(screen.getByText("claims-reviewer")).toBeVisible();
    expect(
      localStorage.getItem("tap-md-drafts-v1:tapper-demo:agent"),
    ).toContain("claims-reviewer");
  });
});
