import {
  CodeOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";

import type { PrototypeCopy } from "./copy";
import type { CatalogItem, CatalogKind } from "./model";

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
  onUpdate,
  onUse,
}: CatalogWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [editingItemId, setEditingItemId] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [draft, setDraft] = useState<CatalogDraft>(EMPTY_DRAFT);
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

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (normalized.length === 0) return items;
    return items.filter((item) =>
      [item.name, item.description, item.instructions].some((value) =>
        value.toLocaleLowerCase().includes(normalized),
      ),
    );
  }, [items, query]);

  const openCreateDialog = () => {
    setEditingItemId(null);
    setDraft(EMPTY_DRAFT);
    setDialogMode("create");
  };

  const openEditDialog = (item: CatalogItem) => {
    setEditingItemId(item.id);
    setDraft({
      description: item.description,
      instructions: item.instructions,
      name: item.name,
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
    if (normalizedDraft.name.length === 0) return;
    if (dialogMode === "edit" && editingItemId !== null) {
      onUpdate(editingItemId, normalizedDraft);
    } else {
      onCreate(normalizedDraft);
    }
    closeDialog();
  };

  const handleDialogKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
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
        <Button
          type="primary"
          icon={<PlusOutlined aria-hidden="true" />}
          onClick={openCreateDialog}
        >
          {createLabel}
        </Button>
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
        <ul className="tap-catalog-list" aria-label={`${heading} catalog`}>
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
                {item.description.length > 0 ? <p>{item.description}</p> : null}
                {item.instructions.length > 0 ? (
                  <div className="tap-catalog-instructions">
                    <strong>{copy.catalog.instructions}</strong>
                    <p>{item.instructions}</p>
                  </div>
                ) : null}
              </article>
              <div className="tap-catalog-actions">
                <Button
                  icon={<EditOutlined aria-hidden="true" />}
                  aria-label={
                    editLabel.startsWith("Edit")
                      ? `Edit ${item.name}`
                      : `${editLabel} ${item.name}`
                  }
                  onClick={() => openEditDialog(item)}
                >
                  {editLabel}
                </Button>
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

      {dialogMode === null ? null : (
        <div className="tap-picker-backdrop">
          <section
            className="tap-catalog-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={dialogMode === "create" ? createLabel : editLabel}
            onKeyDown={handleDialogKeyDown}
          >
            <header>
              <h2>{dialogMode === "create" ? createLabel : editLabel}</h2>
            </header>
            <form onSubmit={saveItem}>
              <label>
                <span>{copy.catalog.name}</span>
                <Input
                  autoFocus
                  aria-label={copy.catalog.name}
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
              <div className="tap-dialog-actions">
                <Button onClick={closeDialog}>{copy.catalog.cancel}</Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  disabled={draft.name.trim().length === 0}
                >
                  {saveLabel}
                </Button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
