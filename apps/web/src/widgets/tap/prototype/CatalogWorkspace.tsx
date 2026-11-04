import {
  CodeOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
} from "react";

import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import type { CatalogItem, CatalogKind } from "./model";
import { previewSkillFile, type SkillPreview } from "./skillImport";
import { PROTOTYPE_TOOLS, ToolsPanel } from "./ToolsPanel";
import { CODEX_MODELS } from "./model";
import { isValidCatalogName, toSkillMarkdown } from "./skillMarkdown";

export interface CatalogDraft {
  description: string;
  instructions: string;
  name: string;
  skillRevisionIds?: string[];
  toolAllowlist?: string[];
  modelAlias?: string;
}

interface CatalogWorkspaceProps {
  copy: PrototypeCopy;
  items: readonly CatalogItem[];
  kind: CatalogKind;
  skills?: readonly CatalogItem[];
  onCreate: (draft: CatalogDraft) => void;
  onUpdate: (itemId: string, draft: CatalogDraft) => void;
  onUse: (itemId: string) => void;
}

const EMPTY_DRAFT: CatalogDraft = {
  description: "",
  instructions: "",
  name: "",
};

export function CatalogWorkspace({
  copy,
  items,
  kind,
  onCreate,
  skills = [],
  onUpdate,
  onUse,
}: CatalogWorkspaceProps) {
  const zh = copy.catalog.createAgent !== "Create agent";
  const [tab, setTab] = useState("catalog");
  const [importPreview, setImportPreview] = useState<SkillPreview | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const selectFile = async (file?: File) => {
    if (!file) return;
    try {
      setImportError(null);
      setImportPreview(await previewSkillFile(file));
    } catch (error) {
      setImportError(
        error instanceof Error ? error.message : "Could not read SKILL.md.",
      );
    }
  };
  const [query, setQuery] = useState("");
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [draft, setDraft] = useState<CatalogDraft>(EMPTY_DRAFT);
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
  const trimmedName = draft.name.trim();
  const isNameInvalid =
    trimmedName.length > 0 && !isValidCatalogName(trimmedName);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (normalized.length === 0) return items;
    return items.filter((item) =>
      [item.name, item.description, item.instructions].some((value) =>
        value.toLocaleLowerCase().includes(normalized),
      ),
    );
  }, [items, query]);

  const openCreateDialog = (event: MouseEvent<HTMLElement>) => {
    dialogTriggerRef.current = event.currentTarget;
    setEditingItemId(null);
    setDraft(EMPTY_DRAFT);
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
      skillRevisionIds: item.skillRevisionIds,
      toolAllowlist: item.toolAllowlist,
      modelAlias: item.modelAlias,
    });
    setDialogMode("edit");
  };

  const closeDialog = () => {
    setDialogMode(null);
    setEditingItemId(null);
  };

  const saveItem = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedDraft = { ...draft, name: draft.name.trim() };
    if (!isValidCatalogName(normalizedDraft.name)) return;
    if (dialogMode === "edit" && editingItemId !== null) {
      onUpdate(editingItemId, normalizedDraft);
    } else {
      onCreate(normalizedDraft);
    }
    closeDialog();
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
        <div className="tap-catalog-actions">
          {isAgent ? null : (
            <label className="tap-catalog-import">
              <span>{zh ? "导入 SKILL.md" : "Import SKILL.md"}</span>
              <input
                type="file"
                aria-label="Import SKILL.md"
                accept=".md"
                onChange={(event) => {
                  void selectFile(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
            </label>
          )}
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={openCreateDialog}
          >
            {createLabel}
          </Button>
        </div>
      </header>

      {isAgent ? (
        <div className="tap-section-tabs" role="tablist" aria-label={heading}>
          <Button
            role="tab"
            aria-selected={tab === "catalog"}
            onClick={() => setTab("catalog")}
          >
            {copy.catalog.agents}
          </Button>
          <Button
            role="tab"
            aria-selected={tab === "tools"}
            onClick={() => setTab("tools")}
          >
            {zh ? "工具" : "Tools"}
          </Button>
        </div>
      ) : null}
      {importError ? <p role="alert">{importError}</p> : null}
      {tab === "tools" ? (
        <ToolsPanel zh={zh} />
      ) : (
        <>
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
                          : copy.catalog.custom}
                      </span>
                    </div>
                    {item.description.length > 0 ? (
                      <p>{item.description}</p>
                    ) : null}
                    {item.instructions.length > 0 ? (
                      <div className="tap-catalog-instructions">
                        <strong>{copy.catalog.instructions}</strong>
                        <p>{item.instructions}</p>
                      </div>
                    ) : null}
                  </article>
                  <div className="tap-catalog-actions">
                    {item.origin === "built-in" ? null : (
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
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {importPreview ? (
        <AccessibleDialog
          ariaLabel="Import SKILL.md"
          className="tap-catalog-dialog"
          onClose={() => setImportPreview(null)}
          opener={dialogTriggerRef.current}
        >
          <h2>{zh ? "导入 SKILL.md" : "Import SKILL.md"}</h2>
          <h3>{importPreview.name}</h3>
          <p>{importPreview.description}</p>
          <p>
            {zh
              ? "请求的工具（不会自动授权）"
              : "Requested tools (not granted)"}
            : {importPreview.requestedTools.join(", ") || "None"}
          </p>
          <pre>{importPreview.instructions}</pre>
          <div className="tap-dialog-actions">
            <Button onClick={() => setImportPreview(null)}>
              {copy.catalog.cancel}
            </Button>
            <Button
              type="primary"
              onClick={() => {
                onCreate(importPreview);
                setImportPreview(null);
              }}
            >
              {zh ? "确认导入" : "Confirm import"}
            </Button>
          </div>
        </AccessibleDialog>
      ) : null}
      {dialogMode === null ? null : (
        <AccessibleDialog
          ariaLabel={dialogMode === "create" ? createLabel : editLabel}
          className="tap-catalog-dialog"
          onClose={closeDialog}
          opener={dialogTriggerRef.current}
        >
          <header>
            <h2>{dialogMode === "create" ? createLabel : editLabel}</h2>
          </header>
          <div
            className={`tap-catalog-dialog-layout${isAgent ? " tap-catalog-dialog-layout--agent" : ""}`}
          >
            <form onSubmit={saveItem}>
              <label>
                <span>{copy.catalog.name}</span>
                <Input
                  aria-label={copy.catalog.name}
                  aria-invalid={isNameInvalid}
                  status={isNameInvalid ? "error" : undefined}
                  value={draft.name}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                />
                <span className="tap-catalog-name-hint">
                  {copy.catalog.nameRule}
                </span>
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
                  rows={4}
                  value={draft.instructions}
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      instructions: event.target.value,
                    }))
                  }
                />
              </label>
              {isAgent ? (
                <>
                  <fieldset>
                    <legend>{copy.catalog.skills}</legend>
                    {skills.map((skill) => (
                      <label key={skill.id}>
                        <input
                          type="checkbox"
                          checked={
                            draft.skillRevisionIds?.includes(skill.id) ?? false
                          }
                          onChange={(event) =>
                            setDraft((current) => ({
                              ...current,
                              skillRevisionIds: event.target.checked
                                ? [
                                    ...(current.skillRevisionIds ?? []),
                                    skill.id,
                                  ]
                                : current.skillRevisionIds?.filter(
                                    (id) => id !== skill.id,
                                  ),
                            }))
                          }
                        />
                        {skill.name}
                      </label>
                    ))}
                  </fieldset>
                  {(["builtin", "mcp"] as const).map((origin) => (
                    <fieldset key={origin}>
                      <legend>
                        {origin === "builtin"
                          ? zh
                            ? "内置工具"
                            : "Built-in tools"
                          : "MCP tools"}
                      </legend>
                      {PROTOTYPE_TOOLS.filter(
                        (tool) => tool.origin === origin,
                      ).map((tool) => (
                        <label key={tool.id}>
                          <input
                            type="checkbox"
                            checked={
                              draft.toolAllowlist?.includes(tool.id) ?? false
                            }
                            onChange={(event) =>
                              setDraft((current) => ({
                                ...current,
                                toolAllowlist: event.target.checked
                                  ? [...(current.toolAllowlist ?? []), tool.id]
                                  : current.toolAllowlist?.filter(
                                      (id) => id !== tool.id,
                                    ),
                              }))
                            }
                          />
                          {tool.name} · {tool.effect}
                        </label>
                      ))}
                    </fieldset>
                  ))}
                  <label>
                    <span>{zh ? "模型" : "Model"}</span>
                    <select
                      aria-label={zh ? "模型" : "Model"}
                      value={draft.modelAlias ?? ""}
                      onChange={(event) =>
                        setDraft({ ...draft, modelAlias: event.target.value })
                      }
                    >
                      <option value="">
                        {zh ? "使用会话模型" : "Use conversation model"}
                      </option>
                      {CODEX_MODELS.map((model) => (
                        <option
                          key={model.id}
                          value={model.id}
                          disabled={!model.available}
                        >
                          {model.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              ) : null}
              <div className="tap-dialog-actions">
                <Button onClick={closeDialog}>{copy.catalog.cancel}</Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  disabled={!isValidCatalogName(draft.name.trim())}
                >
                  {saveLabel}
                </Button>
              </div>
            </form>
            {isAgent ? null : (
              <aside className="tap-catalog-preview">
                <h3>{copy.catalog.previewHeading}</h3>
                <pre aria-label={copy.catalog.previewHeading}>
                  {toSkillMarkdown(draft)}
                </pre>
              </aside>
            )}
          </div>
        </AccessibleDialog>
      )}
    </section>
  );
}
