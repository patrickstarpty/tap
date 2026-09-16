import {
  CodeOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
} from "react";

import { AccessibleDialog } from "./AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import type { CatalogItem, CatalogKind } from "./model";
import { CATALOG_NAME_PATTERN, catalogMarkdown } from "./catalogMarkdown";

export interface CatalogDraft {
  description: string;
  instructions: string;
  name: string;
}

interface CatalogWorkspaceProps {
  copy: PrototypeCopy;
  items: readonly CatalogItem[];
  kind: CatalogKind;
  onCreate: (draft: CatalogDraft) => void;
  onUpdate: (itemId: string, draft: CatalogDraft) => void;
  onUse: (itemId: string) => void;
  readOnly?: boolean;
  durableDrafts?: boolean;
  projectId?: string;
}

const EMPTY_DRAFT: CatalogDraft = {
  description: "",
  instructions: "",
  name: "",
};

function storedDrafts(
  projectId: string | undefined,
  kind: CatalogKind,
): CatalogItem[] {
  if (projectId === undefined || typeof localStorage === "undefined") return [];
  try {
    const value = JSON.parse(
      localStorage.getItem(`tap-md-drafts-v1:${projectId}:${kind}`) ?? "[]",
    ) as unknown;
    if (!Array.isArray(value)) return [];
    return value.filter(
      (item): item is CatalogItem =>
        typeof item === "object" &&
        item !== null &&
        typeof item.id === "string" &&
        item.kind === kind &&
        item.origin === "custom" &&
        typeof item.name === "string" &&
        typeof item.description === "string" &&
        typeof item.instructions === "string",
    );
  } catch {
    return [];
  }
}

export function CatalogWorkspace({
  copy,
  items,
  kind,
  onCreate,
  onUpdate,
  onUse,
  readOnly = false,
  durableDrafts = false,
  projectId,
}: CatalogWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [draft, setDraft] = useState<CatalogDraft>(EMPTY_DRAFT);
  const [localDrafts, setLocalDrafts] = useState<CatalogItem[]>(() =>
    durableDrafts ? storedDrafts(projectId, kind) : [],
  );
  const [validationError, setValidationError] = useState<string | null>(null);
  const dialogTriggerRef = useRef<HTMLElement | null>(null);
  const isAgent = kind === "agent";
  const heading = isAgent ? copy.catalog.agents : copy.catalog.skills;
  const createLabel = isAgent
    ? copy.catalog.createAgent
    : copy.catalog.createSkill;
  const editLabel = isAgent ? copy.catalog.editAgent : copy.catalog.editSkill;
  const saveLabel = isAgent ? copy.catalog.saveAgent : copy.catalog.saveSkill;
  const searchLabel = isAgent
    ? copy.catalog.searchAgents
    : copy.catalog.searchSkills;
  const catalogLabel = isAgent
    ? copy.catalog.agentCatalog
    : copy.catalog.skillCatalog;
  const isChinese = copy.catalog.createAgent !== "Create agent";
  const allItems = durableDrafts ? [...items, ...localDrafts] : items;

  useEffect(() => {
    if (
      !durableDrafts ||
      projectId === undefined ||
      typeof localStorage === "undefined"
    )
      return;
    localStorage.setItem(
      `tap-md-drafts-v1:${projectId}:${kind}`,
      JSON.stringify(localDrafts),
    );
  }, [durableDrafts, kind, localDrafts, projectId]);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (normalized.length === 0) return allItems;
    return allItems.filter((item) =>
      [item.name, item.description, item.instructions].some((value) =>
        value.toLocaleLowerCase().includes(normalized),
      ),
    );
  }, [allItems, query]);

  const openCreateDialog = (event: MouseEvent<HTMLElement>) => {
    dialogTriggerRef.current = event.currentTarget;
    setEditingItemId(null);
    setDraft(EMPTY_DRAFT);
    setValidationError(null);
    setDialogMode("create");
  };

  const openEditDialog = (
    item: CatalogItem,
    event: MouseEvent<HTMLElement>,
  ) => {
    dialogTriggerRef.current = event.currentTarget;
    setEditingItemId(item.id);
    setDraft({
      description: item.description,
      instructions: item.instructions,
      name: item.name,
    });
    setValidationError(null);
    setDialogMode("edit");
  };

  const closeDialog = () => {
    setDialogMode(null);
    setEditingItemId(null);
  };

  const saveItem = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedDraft = { ...draft, name: draft.name.trim() };
    if (normalizedDraft.name.length === 0) return;
    if (durableDrafts) {
      try {
        catalogMarkdown(kind, normalizedDraft);
      } catch {
        setValidationError(
          isChinese
            ? "请填写小写连字符名称、用途描述和 Markdown 指令。"
            : "Enter a lowercase kebab-case name, description, and Markdown instructions.",
        );
        return;
      }
      setLocalDrafts((current) =>
        dialogMode === "edit" && editingItemId !== null
          ? current.map((item) =>
              item.id === editingItemId
                ? { ...item, ...normalizedDraft }
                : item,
            )
          : [
              ...current,
              {
                id: `local-${kind}-${crypto.randomUUID()}`,
                kind,
                origin: "custom",
                ...normalizedDraft,
              },
            ],
      );
      closeDialog();
      return;
    }
    if (dialogMode === "edit" && editingItemId !== null) {
      onUpdate(editingItemId, normalizedDraft);
    } else {
      onCreate(normalizedDraft);
    }
    closeDialog();
  };

  const downloadDraft = (item: CatalogItem) => {
    const file = catalogMarkdown(kind, item);
    const url = URL.createObjectURL(
      new Blob([file.content], { type: "text/markdown;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section
      className="tap-module tap-catalog"
      aria-labelledby={`${kind}-heading`}
    >
      <header className="tap-module-heading">
        <div>
          <h1 id={`${kind}-heading`}>{heading}</h1>
          <p>
            {isAgent
              ? copy.catalog.agentsDescription
              : copy.catalog.skillsDescription}
          </p>
        </div>
        {readOnly ? null : (
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={openCreateDialog}
          >
            {durableDrafts
              ? `${createLabel}${isChinese ? "草稿" : " draft"}`
              : createLabel}
          </Button>
        )}
      </header>

      <div className="tap-catalog-toolbar">
        <Input
          aria-label={searchLabel}
          placeholder={searchLabel}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span>{visibleItems.length}</span>
      </div>

      {visibleItems.length === 0 ? (
        <div className="tap-catalog-empty">{copy.catalog.noResults}</div>
      ) : (
        <ul className="tap-catalog-list" aria-label={catalogLabel}>
          {visibleItems.map((item) => (
            <li key={item.id} aria-label={item.name}>
              <div className="tap-catalog-icon" aria-hidden="true">
                {isAgent ? <MessageOutlined /> : <CodeOutlined />}
              </div>
              <article>
                <div className="tap-catalog-title">
                  <h2>{item.name}</h2>
                  <span data-origin={item.origin}>
                    {item.origin === "built-in"
                      ? copy.catalog.builtIn
                      : durableDrafts
                        ? isChinese
                          ? "本地草稿"
                          : "Local draft"
                        : copy.catalog.custom}
                  </span>
                </div>
                {item.description.length > 0 ? <p>{item.description}</p> : null}
                {item.instructions.length > 0 ? (
                  <div className="tap-catalog-instructions">
                    <strong>{copy.catalog.instructions}</strong>
                    <p>{item.instructions}</p>
                  </div>
                ) : null}
              </article>
              <div className="tap-catalog-actions">
                {readOnly ||
                (durableDrafts && item.origin === "built-in") ? null : (
                  <Button
                    icon={<EditOutlined aria-hidden="true" />}
                    aria-label={
                      editLabel.startsWith("Edit")
                        ? `Edit ${item.name}`
                        : `${editLabel} ${item.name}`
                    }
                    onClick={(event) => openEditDialog(item, event)}
                  >
                    {editLabel}
                  </Button>
                )}
                {durableDrafts && item.origin === "custom" ? (
                  <Button onClick={() => downloadDraft(item)}>
                    {isChinese ? "下载 Markdown" : "Download Markdown"}
                  </Button>
                ) : null}
                {durableDrafts && item.origin === "custom" ? null : (
                  <Button
                    type="primary"
                    ghost
                    aria-label={
                      copy.catalog.useInChat === "Use in chat"
                        ? `Use ${item.name} in chat`
                        : `${copy.catalog.useInChat} ${item.name}`
                    }
                    onClick={() => onUse(item.id)}
                  >
                    {copy.catalog.useInChat}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {dialogMode === null ? null : (
        <AccessibleDialog
          ariaLabel={dialogMode === "create" ? createLabel : editLabel}
          className="tap-catalog-dialog"
          onClose={closeDialog}
          opener={dialogTriggerRef.current}
        >
          <header>
            <h2>
              {dialogMode === "create"
                ? durableDrafts
                  ? `${createLabel}${isChinese ? "草稿" : " draft"}`
                  : createLabel
                : editLabel}
            </h2>
            {durableDrafts ? (
              <p>
                {isAgent
                  ? isChinese
                    ? "生成带 YAML 元数据的 Agent .md 文件。"
                    : "Create an Agent .md file with YAML frontmatter."
                  : isChinese
                    ? "生成符合 Agent Skills 规范的 SKILL.md。"
                    : "Create an Agent Skills SKILL.md file."}
              </p>
            ) : null}
          </header>
          <form onSubmit={saveItem}>
            <label>
              <span>
                {durableDrafts
                  ? isChinese
                    ? "名称（小写连字符）"
                    : "Name (kebab-case)"
                  : copy.catalog.name}
              </span>
              <Input
                aria-label={
                  durableDrafts
                    ? isChinese
                      ? "名称（小写连字符）"
                      : "Name (kebab-case)"
                    : copy.catalog.name
                }
                value={draft.name}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
              />
            </label>
            <label>
              <span>{copy.catalog.description}</span>
              <Input.TextArea
                aria-label={copy.catalog.description}
                rows={2}
                value={draft.description}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
              />
            </label>
            <label>
              <span>{copy.catalog.instructions}</span>
              <Input.TextArea
                aria-label={copy.catalog.instructions}
                rows={durableDrafts ? 8 : 4}
                value={draft.instructions}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    instructions: event.target.value,
                  }))
                }
              />
            </label>
            {durableDrafts ? (
              <div className="tap-catalog-markdown-preview">
                <strong>
                  {isChinese ? "Markdown 文件结构" : "Markdown file structure"}
                </strong>
                <pre>{`---\nname: ${draft.name.trim() || "agent-name"}\ndescription: ${JSON.stringify(draft.description.trim() || "When to use this agent or skill")}\n---\n\n${draft.instructions.trim() || "# Instructions"}`}</pre>
              </div>
            ) : null}
            {validationError ? <p role="alert">{validationError}</p> : null}
            <div className="tap-dialog-actions">
              <Button onClick={closeDialog}>{copy.catalog.cancel}</Button>
              <Button
                type="primary"
                htmlType="submit"
                disabled={
                  durableDrafts
                    ? !CATALOG_NAME_PATTERN.test(draft.name.trim()) ||
                      draft.name.trim().length > 64 ||
                      draft.description.trim().length === 0 ||
                      draft.instructions.trim().length === 0
                    : draft.name.trim().length === 0
                }
              >
                {saveLabel}
              </Button>
            </div>
          </form>
        </AccessibleDialog>
      )}
    </section>
  );
}
