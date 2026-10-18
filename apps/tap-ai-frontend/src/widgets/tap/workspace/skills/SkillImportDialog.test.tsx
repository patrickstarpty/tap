import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useCommitSkillImport,
  useStageSkillImport,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { SkillImportDialog } from "./SkillImportDialog";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useStageSkillImport: vi.fn(),
      useCommitSkillImport: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

function renderDialog(
  overrides: {
    stageMutateAsync?: ReturnType<typeof vi.fn>;
    commitMutateAsync?: ReturnType<typeof vi.fn>;
    onClose?: () => void;
    onImported?: (result: unknown) => void;
  } = {},
) {
  const stageMutateAsync = overrides.stageMutateAsync ?? vi.fn();
  const commitMutateAsync = overrides.commitMutateAsync ?? vi.fn();
  vi.mocked(useStageSkillImport).mockReturnValue({
    mutateAsync: stageMutateAsync,
    isPending: false,
  } as never);
  vi.mocked(useCommitSkillImport).mockReturnValue({
    mutateAsync: commitMutateAsync,
    isPending: false,
  } as never);
  const onClose = overrides.onClose ?? vi.fn();
  const onImported = overrides.onImported ?? vi.fn();
  return {
    onClose,
    onImported,
    stageMutateAsync,
    commitMutateAsync,
    ...render(
      createElement(SkillImportDialog, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        onClose,
        onImported,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

const PREVIEW_NO_CONFLICT = {
  importId: "import_1",
  expiresAt: "2026-10-18T00:00:00Z",
  skills: [
    {
      root: "policy-lookup",
      name: "policy-lookup",
      description: "Look up policy terms",
      frontmatter: { name: "policy-lookup" },
      files: [
        {
          relativePath: "SKILL.md",
          category: "skill",
          attached: true,
          sizeBytes: 10,
          sha256: "a",
        },
        {
          relativePath: "references/guide.md",
          category: "reference",
          attached: true,
          sizeBytes: 20,
          sha256: "b",
        },
        {
          relativePath: "references/unused.md",
          category: "reference",
          attached: false,
          sizeBytes: 5,
          sha256: "c",
        },
        {
          relativePath: "scripts/run.py",
          category: "script",
          attached: false,
          sizeBytes: 15,
          sha256: "d",
        },
        {
          relativePath: "assets/logo.png",
          category: "asset",
          attached: false,
          sizeBytes: 30,
          sha256: "e",
        },
      ],
      warnings: ["A reference file was not attached."],
    },
  ],
};

function fileWithRelativePath(
  contents: string,
  name: string,
  relativePath: string,
): File {
  const file = new File([contents], name, { type: "text/markdown" });
  Object.defineProperty(file, "webkitRelativePath", {
    value: relativePath,
  });
  return file;
}

describe("SkillImportDialog step 1", () => {
  it("stages a single .md file as the package's SKILL.md, regardless of its picked filename", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });

    const file = new File(
      ["---\nname: policy-lookup\n---\nBody"],
      "policy-lookup.md",
      {
        type: "text/markdown",
      },
    );
    await user.upload(screen.getByLabelText("Browse files"), file);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(stageMutateAsync).toHaveBeenCalledWith({
      files: [{ file, relativePath: "SKILL.md" }],
    });
  });

  it("stages a .zip file as an archive", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });

    const archive = new File(["zip-bytes"], "skill-package.zip", {
      type: "application/zip",
    });
    await user.upload(screen.getByLabelText("Browse files"), archive);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(stageMutateAsync).toHaveBeenCalledWith({ archive });
  });

  it("stages a selected folder's files with their webkitRelativePath", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });

    const skillFile = fileWithRelativePath(
      "---\nname: policy-lookup\n---\nBody",
      "SKILL.md",
      "policy-lookup/SKILL.md",
    );
    const referenceFile = fileWithRelativePath(
      "Reference body",
      "guide.md",
      "policy-lookup/references/guide.md",
    );
    await user.upload(screen.getByLabelText("Browse folder"), [
      skillFile,
      referenceFile,
    ]);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(stageMutateAsync).toHaveBeenCalledWith({
      files: [
        { file: skillFile, relativePath: "policy-lookup/SKILL.md" },
        {
          file: referenceFile,
          relativePath: "policy-lookup/references/guide.md",
        },
      ],
    });
  });

  it("stages a pasted SKILL.md text and moves to the review step", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });

    await user.type(
      screen.getByRole("textbox", { name: "Paste SKILL.md content" }),
      "---\nname: policy-lookup\n---\nBody",
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));

    expect(stageMutateAsync).toHaveBeenCalledWith({
      text: "---\nname: policy-lookup\n---\nBody",
    });
    expect(await screen.findByText("Review before import")).toBeVisible();
  });
});

describe("SkillImportDialog step 2", () => {
  it("shows per-file categories and warnings for the staged preview", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });
    await user.type(
      screen.getByRole("textbox", { name: "Paste SKILL.md content" }),
      "text",
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));

    const item = await screen.findByText("policy-lookup");
    const container =
      item.closest("li") ?? item.closest("article") ?? document.body;
    expect(
      within(container as HTMLElement).getByText("Instructions"),
    ).toBeVisible();
    expect(
      within(container as HTMLElement).getByText("Reference · attached"),
    ).toBeVisible();
    expect(
      within(container as HTMLElement).getByText("Reference · not attached"),
    ).toBeVisible();
    expect(
      within(container as HTMLElement).getByText(
        "Script · saved, not executed",
      ),
    ).toBeVisible();
    expect(within(container as HTMLElement).getByText("Asset")).toBeVisible();
    expect(
      within(container as HTMLElement).getByText(
        "A reference file was not attached.",
      ),
    ).toBeVisible();
  });

  it("offers import-as-new-revision or skip for a name conflict, defaulting to skip", async () => {
    const user = userEvent.setup();
    const preview = {
      ...PREVIEW_NO_CONFLICT,
      skills: [
        {
          ...PREVIEW_NO_CONFLICT.skills[0],
          conflictSkillId: "skill_existing",
        },
      ],
    };
    const stageMutateAsync = vi.fn().mockResolvedValue(preview);
    const commitMutateAsync = vi.fn().mockResolvedValue({ revisions: [] });
    renderDialog({ stageMutateAsync, commitMutateAsync });
    await user.type(
      screen.getByRole("textbox", { name: "Paste SKILL.md content" }),
      "text",
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("A skill with this name already exists.");

    // Defaults to skip -- committing sends no selection for this item.
    await user.click(screen.getByRole("button", { name: "Import selected" }));
    expect(commitMutateAsync).toHaveBeenCalledWith({
      importId: "import_1",
      selections: [],
    });

    // Switching to "import as a new revision" includes it.
    commitMutateAsync.mockClear();
    await user.click(
      screen.getByRole("radio", { name: "Import as a new revision" }),
    );
    await user.click(screen.getByRole("button", { name: "Import selected" }));
    expect(commitMutateAsync).toHaveBeenCalledWith({
      importId: "import_1",
      selections: [
        { root: "policy-lookup", asNewRevisionOf: "skill_existing" },
      ],
    });
  });

  it("commits the selected skills and reports the result", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    const commitResult = { revisions: [{ revisionId: "rev_new" }] };
    const commitMutateAsync = vi.fn().mockResolvedValue(commitResult);
    const onImported = vi.fn();
    renderDialog({ stageMutateAsync, commitMutateAsync, onImported });
    await user.type(
      screen.getByRole("textbox", { name: "Paste SKILL.md content" }),
      "text",
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("policy-lookup");

    await user.click(screen.getByRole("button", { name: "Import selected" }));

    expect(commitMutateAsync).toHaveBeenCalledWith({
      importId: "import_1",
      selections: [{ root: "policy-lookup", asNewRevisionOf: null }],
    });
    expect(onImported).toHaveBeenCalledWith(commitResult);
  });

  it("goes back to step 1 without losing the ability to retry", async () => {
    const user = userEvent.setup();
    const stageMutateAsync = vi.fn().mockResolvedValue(PREVIEW_NO_CONFLICT);
    renderDialog({ stageMutateAsync });
    await user.type(
      screen.getByRole("textbox", { name: "Paste SKILL.md content" }),
      "text",
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await screen.findByText("Review before import");

    await user.click(screen.getByRole("button", { name: "Back" }));

    expect(screen.getByText("Import a skill")).toBeVisible();
  });
});
