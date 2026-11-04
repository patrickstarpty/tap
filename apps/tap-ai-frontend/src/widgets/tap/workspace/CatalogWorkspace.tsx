import {
  CodeOutlined,
  EditOutlined,
  MessageOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
} from "react";
import {
  catalogErrorMessage,
  fetchSkillContent,
  fetchSkillDownload,
  managementCatalog,
  type AgentRevisionView,
  type SkillRevisionView,
  type ToolView,
  type McpServerView,
} from "../../../features/knowledge/api/aiAssets";
import {
  fetchModelCatalog,
  type ModelCatalogItem,
} from "../../../features/knowledge/api/modelCatalog";
import { AccessibleDialog } from "./AccessibleDialog";
import type { WorkspaceCopy } from "./copy";
import type { CatalogItem, CatalogKind } from "./model";
import { CATALOG_NAME_PATTERN } from "./catalogMarkdown";
import { previewSkillFile, type SkillPreview } from "./skillImport";
import { ToolsPanel } from "./ToolsPanel";

export interface CatalogDraft {
  description: string;
  instructions: string;
  name: string;
  skillRevisionIds?: string[];
  toolAllowlist?: string[];
  modelAlias?: string;
}
interface CatalogWorkspaceProps {
  copy: WorkspaceCopy;
  items: readonly CatalogItem[];
  kind: CatalogKind;
  onUse: (itemId: string) => void;
  readOnly?: boolean;
  projectId?: string;
  onChanged?: () => Promise<unknown>;
}
type Revision = AgentRevisionView | SkillRevisionView;
const EMPTY: CatalogDraft = {
  description: "",
  instructions: "",
  name: "",
  skillRevisionIds: [],
  toolAllowlist: [],
  modelAlias: "",
};

export function CatalogWorkspace(props: CatalogWorkspaceProps) {
  return (
    <CatalogWorkspaceContent
      key={`${props.projectId}:${props.kind}`}
      {...props}
    />
  );
}
function CatalogWorkspaceContent({
  copy,
  kind,
  onUse,
  readOnly = false,
  projectId,
  onChanged,
}: CatalogWorkspaceProps) {
  const zh = copy.catalog.createAgent !== "Create agent";
  const t = (en: string, cn: string) => (zh ? cn : en);
  const isAgent = kind === "agent";
  const heading = isAgent ? copy.catalog.agents : copy.catalog.skills;
  const createLabel = isAgent
    ? copy.catalog.createAgent
    : copy.catalog.createSkill;
  const editLabel = isAgent ? copy.catalog.editAgent : copy.catalog.editSkill;
  const saveLabel = isAgent ? copy.catalog.saveAgent : copy.catalog.saveSkill;
  const [query, setQuery] = useState("");
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [skills, setSkills] = useState<SkillRevisionView[]>([]);
  const [tools, setTools] = useState<ToolView[]>([]);
  const [servers, setServers] = useState<McpServerView[]>([]);
  const [models, setModels] = useState<ModelCatalogItem[]>([]);
  const [loading, setLoading] = useState(projectId !== undefined);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [tab, setTab] = useState<"catalog" | "tools">("catalog");
  const [mode, setMode] = useState<
    "create" | "edit" | "import" | "history" | null
  >(null);
  const [editing, setEditing] = useState<Revision | null>(null);
  const [draft, setDraft] = useState<CatalogDraft>(EMPTY);
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const opener = useRef<HTMLElement | null>(null);
  const alive = useRef(true);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const contentRequest = useRef(0);
  const load = async () => {
    if (!projectId) return;
    const list = isAgent
      ? await managementCatalog.agents(projectId)
      : await managementCatalog.skills(projectId);
    if (!alive.current) return;
    setRevisions(list.items);
    if (isAgent) {
      const [s, t, srv, m] = await Promise.all([
        managementCatalog.skills(projectId),
        managementCatalog.tools(projectId),
        managementCatalog.servers(projectId),
        fetchModelCatalog(projectId),
      ]);
      if (!alive.current) return;
      setSkills(s.items);
      setTools(t.items);
      setServers(srv.items);
      setModels(m.items);
    }
  };
  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      await load();
    } catch (e) {
      if (alive.current) setError(catalogErrorMessage(e, zh));
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => {
    alive.current = true;
    void reload();
    return () => {
      alive.current = false;
    };
  }, []); // The keyed workspace owns one project and catalog.
  const run = async (action: () => Promise<unknown>, close = true) => {
    setPending(true);
    setError(null);
    try {
      await action();
      await load();
      await onChanged?.();
      if (close) setMode(null);
    } catch (e) {
      setError(catalogErrorMessage(e, zh));
    } finally {
      setPending(false);
    }
  };
  const open = (event: MouseEvent<HTMLElement>, revision?: Revision) => {
    opener.current = event.currentTarget;
    setError(null);
    setEditing(revision ?? null);
    setDraft(
      revision
        ? {
            name: revision.displayName,
            description: revision.description ?? "",
            instructions:
              "systemInstruction" in revision
                ? (revision.systemInstruction ?? "")
                : ((revision as SkillRevisionView).instructionTemplate ?? ""),
            skillRevisionIds:
              "skillRevisionIds" in revision ? revision.skillRevisionIds : [],
            toolAllowlist:
              "toolAllowlist" in revision ? revision.toolAllowlist : [],
            modelAlias:
              "modelAlias" in revision ? (revision.modelAlias ?? "") : "",
          }
        : EMPTY,
    );
    setMode(revision ? "edit" : "create");
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!projectId) return;
    void run(() =>
      isAgent
        ? managementCatalog.saveAgent(
            projectId,
            {
              displayName: draft.name.trim(),
              description: draft.description,
              systemInstruction: draft.instructions,
              skillRevisionIds: draft.skillRevisionIds,
              toolAllowlist: draft.toolAllowlist,
              modelAlias: draft.modelAlias || null,
            },
            editing?.assetId,
          )
        : managementCatalog.saveSkill(projectId, {
            name: draft.name.trim(),
            description: draft.description,
            instructions: draft.instructions,
          }),
    );
  };
  const importPreview = async (file?: File) => {
    if (!file) return;
    setError(null);
    try {
      const value = await previewSkillFile(file);
      setPreview(value);
      setImportFile(file);
      setMode("import");
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : t("Could not read SKILL.md.", "无法读取 SKILL.md。"),
      );
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };
  const loadContent = async (revisionId: string) => {
    if (!projectId) return;
    const requestId = ++contentRequest.current;
    setContent(null);
    try {
      const original = await fetchSkillContent(projectId, revisionId);
      if (alive.current && requestId === contentRequest.current)
        setContent(original);
    } catch (error) {
      if (alive.current && requestId === contentRequest.current)
        setError(catalogErrorMessage(error, zh));
    }
  };
  const history = (revision: Revision, event: MouseEvent<HTMLElement>) => {
    opener.current = event.currentTarget;
    setEditing(revision);
    setError(null);
    setMode("history");
    if (!isAgent) void loadContent(revision.revisionId);
  };
  const download = async (revision: Revision) => {
    if (!projectId) return;
    await run(async () => {
      const source = await fetchSkillDownload(projectId, revision.revisionId);
      const url = URL.createObjectURL(source);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "SKILL.md";
      anchor.click();
      URL.revokeObjectURL(url);
    }, false);
  };
  const latest = revisions.filter(
    (revision, index) =>
      !revisions
        .slice(0, index)
        .some((other) => other.assetId === revision.assetId),
  );
  const visible = latest.filter((r) =>
    `${r.displayName} ${r.description ?? ""}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  const toggle = (key: "skillRevisionIds" | "toolAllowlist", id: string) =>
    setDraft((current) => ({
      ...current,
      [key]: (current[key] ?? []).includes(id)
        ? current[key]!.filter((value) => value !== id)
        : [...(current[key] ?? []), id],
    }));
  const canSave =
    draft.name.trim().length > 0 &&
    draft.name.length <= (isAgent ? 128 : 64) &&
    (isAgent || CATALOG_NAME_PATTERN.test(draft.name.trim())) &&
    draft.description.trim().length > 0 &&
    draft.description.length <= 1024 &&
    draft.instructions.trim().length > 0;
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
          <div className="tap-catalog-actions">
            {!isAgent ? (
              <label className="tap-catalog-import">
                <span>{t("Import SKILL.md", "导入 SKILL.md")}</span>
                <input
                  ref={fileInput}
                  aria-label="Import SKILL.md"
                  type="file"
                  accept=".md,text/markdown"
                  disabled={!projectId || pending}
                  onChange={(event) =>
                    void importPreview(event.target.files?.[0])
                  }
                />
              </label>
            ) : null}
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              disabled={!projectId || pending}
              onClick={(event) => open(event)}
            >
              {createLabel}
            </Button>
          </div>
        )}
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
            {t("Tools", "工具")}
          </Button>
        </div>
      ) : null}
      {error ? (
        <div role="alert">
          <p>{error}</p>
          {mode === null ? (
            <Button onClick={() => void reload()}>{t("Retry", "重试")}</Button>
          ) : null}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <p role="status">
          {t(
            "Requested tools are recorded but not granted.",
            "请求的工具仅记录，不会自动授权。",
          )}{" "}
          {warnings.join(", ")}
        </p>
      ) : null}
      {loading ? (
        <p role="status">{t("Loading catalog…", "正在加载目录…")}</p>
      ) : null}
      {tab === "tools" && projectId ? (
        <ToolsPanel
          projectId={projectId}
          tools={tools}
          servers={servers}
          zh={zh}
          onChanged={load}
        />
      ) : (
        <>
          <div className="tap-catalog-toolbar">
            <Input
              aria-label={
                isAgent ? copy.catalog.searchAgents : copy.catalog.searchSkills
              }
              placeholder={
                isAgent ? copy.catalog.searchAgents : copy.catalog.searchSkills
              }
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <span>{visible.length}</span>
          </div>
          {!loading && visible.length === 0 ? (
            <div className="tap-catalog-empty">{copy.catalog.noResults}</div>
          ) : null}
          <ul
            className="tap-catalog-list"
            aria-label={
              isAgent ? copy.catalog.agentCatalog : copy.catalog.skillCatalog
            }
          >
            {visible.map((item) => (
              <li key={item.revisionId} aria-label={item.displayName}>
                <div className="tap-catalog-icon" aria-hidden="true">
                  {isAgent ? <MessageOutlined /> : <CodeOutlined />}
                </div>
                <article>
                  <div className="tap-catalog-title">
                    <h2>{item.displayName}</h2>
                    <span
                      data-origin={
                        item.origin === "seed" ? "built-in" : "custom"
                      }
                    >
                      {item.origin === "seed"
                        ? copy.catalog.builtIn
                        : t("Custom", "自定义")}
                    </span>
                    <span>
                      {item.status === "enabled"
                        ? t("Enabled", "已启用")
                        : t("Disabled", "已停用")}
                    </span>
                  </div>
                  <p>{item.description}</p>
                  <div className="tap-catalog-instructions">
                    <strong>{copy.catalog.instructions}</strong>
                    <p>
                      {"systemInstruction" in item
                        ? item.systemInstruction
                        : (item as SkillRevisionView).instructionTemplate}
                    </p>
                  </div>
                </article>
                <div className="tap-catalog-actions">
                  {readOnly || item.origin === "seed" ? null : (
                    <Button
                      icon={<EditOutlined aria-hidden="true" />}
                      aria-label={`Edit ${item.displayName}`}
                      onClick={(event) => open(event, item)}
                    >
                      {editLabel}
                    </Button>
                  )}
                  <Button onClick={(event) => void history(item, event)}>
                    {t("Revision history", "修订历史")}
                  </Button>
                  {!isAgent ? (
                    <Button
                      disabled={pending}
                      onClick={() => void download(item)}
                    >
                      {t("Download Markdown", "下载 Markdown")}
                    </Button>
                  ) : null}
                  {readOnly || item.origin === "seed" ? null : (
                    <Button
                      disabled={pending}
                      onClick={() =>
                        void run(
                          () =>
                            managementCatalog.status(
                              projectId!,
                              kind,
                              item.revisionId,
                              item.status === "enabled"
                                ? "disabled"
                                : "enabled",
                            ),
                          false,
                        )
                      }
                    >
                      {item.status === "enabled"
                        ? t("Disable", "停用")
                        : t("Enable", "启用")}
                    </Button>
                  )}
                  <Button
                    type="primary"
                    ghost
                    disabled={item.status !== "enabled"}
                    aria-label={
                      copy.catalog.useInChat === "Use in chat"
                        ? `Use ${item.displayName} in chat`
                        : `${copy.catalog.useInChat} ${item.displayName}`
                    }
                    onClick={() => onUse(item.revisionId)}
                  >
                    {copy.catalog.useInChat}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {mode ? (
        <AccessibleDialog
          ariaLabel={
            mode === "import"
              ? t("Import SKILL.md", "导入 SKILL.md")
              : mode === "history"
                ? t("Revision history", "修订历史")
                : mode === "create"
                  ? createLabel
                  : editLabel
          }
          className="tap-catalog-dialog"
          onClose={() => {
            if (!pending) setMode(null);
          }}
          opener={opener.current}
        >
          <header>
            <h2>
              {mode === "import"
                ? t("Import SKILL.md", "导入 SKILL.md")
                : mode === "history"
                  ? t("Revision history", "修订历史")
                  : mode === "create"
                    ? createLabel
                    : editLabel}
            </h2>
          </header>
          {error ? <p role="alert">{error}</p> : null}
          {mode === "import" && preview ? (
            <>
              <h3>{preview.name}</h3>
              <p>{preview.description}</p>
              <p>
                {t(
                  "Requested tools (not granted)",
                  "请求的工具（不会自动授权）",
                )}
                : {preview.requestedTools.join(", ") || t("None", "无")}
              </p>
              <pre>{preview.instructions}</pre>
              <div className="tap-dialog-actions">
                <Button onClick={() => setMode(null)} disabled={pending}>
                  {copy.catalog.cancel}
                </Button>
                <Button
                  type="primary"
                  loading={pending}
                  onClick={() =>
                    void run(async () => {
                      const result = await managementCatalog.importSkill(
                        projectId!,
                        importFile!,
                      );
                      setWarnings(result.warnings ?? []);
                    })
                  }
                >
                  {t("Confirm import", "确认导入")}
                </Button>
              </div>
            </>
          ) : null}
          {mode === "history" && editing ? (
            <>
              <ul>
                {revisions
                  .filter((r) => r.assetId === editing.assetId)
                  .map((revision) => (
                    <li key={revision.revisionId}>
                      <Button
                        onClick={() => {
                          setEditing(revision);
                          if (!isAgent) void loadContent(revision.revisionId);
                        }}
                      >
                        {revision.revisionId} · {revision.status}
                      </Button>
                      {!readOnly ? (
                        <Button
                          disabled={pending}
                          aria-label={`${revision.status === "enabled" ? "Disable" : "Enable"} ${revision.revisionId}`}
                          onClick={() =>
                            void run(() =>
                              managementCatalog.status(
                                projectId!,
                                kind,
                                revision.revisionId,
                                revision.status === "enabled"
                                  ? "disabled"
                                  : "enabled",
                              ),
                            )
                          }
                        >
                          {revision.status === "enabled"
                            ? t("Disable", "停用")
                            : t("Enable", "启用")}
                        </Button>
                      ) : null}
                    </li>
                  ))}
              </ul>
              {isAgent ? (
                <>
                  <h3>{editing.displayName}</h3>
                  <p>{editing.description}</p>
                  <strong>{copy.catalog.instructions}</strong>
                  <pre>{(editing as AgentRevisionView).systemInstruction}</pre>
                  <p>
                    {t("Skills", "技能")}:{" "}
                    {(editing as AgentRevisionView).skillRevisionIds
                      ?.map(
                        (id) =>
                          skills.find((skill) => skill.revisionId === id)
                            ?.displayName ?? id,
                      )
                      .join(", ") || t("None", "无")}
                  </p>
                  <p>
                    {t("Tools", "工具")}:{" "}
                    {(editing as AgentRevisionView).toolAllowlist
                      .map(
                        (id) =>
                          tools.find((tool) => tool.toolId === id)
                            ?.displayName ?? id,
                      )
                      .join(", ") || t("None", "无")}
                  </p>
                  <p>
                    {t("Model", "模型")}:{" "}
                    {(editing as AgentRevisionView).modelAlias ||
                      t("Use conversation model", "使用会话模型")}
                  </p>
                </>
              ) : (
                <pre>{content ?? t("Loading original…", "正在加载原文…")}</pre>
              )}
              <Button onClick={() => setMode(null)}>
                {t("Close", "关闭")}
              </Button>
            </>
          ) : null}
          {mode === "create" || mode === "edit" ? (
            <form onSubmit={save}>
              <label>
                <span>
                  {isAgent
                    ? t("Name", "名称")
                    : t("Name (kebab-case)", "名称（小写连字符）")}
                </span>
                <Input
                  aria-label={
                    isAgent
                      ? t("Name", "名称")
                      : t("Name (kebab-case)", "名称（小写连字符）")
                  }
                  required
                  value={draft.name}
                  disabled={!isAgent && mode === "edit"}
                  onChange={(event) =>
                    setDraft({ ...draft, name: event.target.value })
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
                    setDraft({ ...draft, description: event.target.value })
                  }
                />
              </label>
              <label>
                <span>{copy.catalog.instructions}</span>
                <Input.TextArea
                  aria-label={copy.catalog.instructions}
                  rows={6}
                  value={draft.instructions}
                  onChange={(event) =>
                    setDraft({ ...draft, instructions: event.target.value })
                  }
                />
              </label>
              {isAgent ? (
                <>
                  <fieldset>
                    <legend>{copy.catalog.skills}</legend>
                    {skills
                      .filter(
                        (s) =>
                          s.status === "enabled" ||
                          draft.skillRevisionIds?.includes(s.revisionId),
                      )
                      .map((skill) => (
                        <label key={skill.revisionId}>
                          <input
                            type="checkbox"
                            disabled={
                              skill.status !== "enabled" &&
                              !draft.skillRevisionIds?.includes(
                                skill.revisionId,
                              )
                            }
                            checked={
                              draft.skillRevisionIds?.includes(
                                skill.revisionId,
                              ) ?? false
                            }
                            onChange={() =>
                              toggle("skillRevisionIds", skill.revisionId)
                            }
                          />
                          {skill.displayName} · {skill.revisionId}
                          {skill.status !== "enabled"
                            ? t(" (disabled)", "（已停用）")
                            : ""}
                        </label>
                      ))}
                  </fieldset>
                  {(["builtin", "mcp"] as const).map((origin) => (
                    <fieldset key={origin}>
                      <legend>
                        {origin === "builtin"
                          ? t("Built-in tools", "内置工具")
                          : "MCP tools"}
                      </legend>
                      {tools
                        .filter((tool) => tool.origin === origin)
                        .map((tool) => (
                          <label key={tool.toolId}>
                            <input
                              type="checkbox"
                              disabled={
                                !tool.enabled &&
                                !draft.toolAllowlist?.includes(tool.toolId)
                              }
                              checked={
                                draft.toolAllowlist?.includes(tool.toolId) ??
                                false
                              }
                              onChange={() =>
                                toggle("toolAllowlist", tool.toolId)
                              }
                            />
                            {tool.displayName} · {tool.effect}
                          </label>
                        ))}
                    </fieldset>
                  ))}
                  {draft.skillRevisionIds
                    ?.filter(
                      (id) => !skills.some((skill) => skill.revisionId === id),
                    )
                    .map((id) => (
                      <label key={id}>
                        <input
                          type="checkbox"
                          checked
                          onChange={() => toggle("skillRevisionIds", id)}
                        />
                        {t("Unavailable Skill", "技能不可用")}: {id}
                      </label>
                    ))}
                  {draft.toolAllowlist
                    ?.filter((id) => !tools.some((tool) => tool.toolId === id))
                    .map((id) => (
                      <label key={id}>
                        <input
                          type="checkbox"
                          checked
                          onChange={() => toggle("toolAllowlist", id)}
                        />
                        {t("Unavailable tool", "工具不可用")}: {id}
                      </label>
                    ))}
                  <label>
                    <span>{t("Model", "模型")}</span>
                    <select
                      aria-label={t("Model", "模型")}
                      value={draft.modelAlias}
                      onChange={(event) =>
                        setDraft({ ...draft, modelAlias: event.target.value })
                      }
                    >
                      <option value="">
                        {t("Use conversation model", "使用会话模型")}
                      </option>
                      {models
                        .filter((model) =>
                          model.capabilities.includes("structured"),
                        )
                        .map((model) => (
                          <option key={model.alias} value={model.alias}>
                            {model.displayName}
                          </option>
                        ))}
                    </select>
                  </label>
                </>
              ) : null}
              <p>
                {t(
                  "Saving creates an immutable revision.",
                  "保存会创建不可变修订。",
                )}
              </p>
              <div className="tap-dialog-actions">
                <Button disabled={pending} onClick={() => setMode(null)}>
                  {copy.catalog.cancel}
                </Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={pending}
                  disabled={!canSave || loading}
                >
                  {saveLabel}
                </Button>
              </div>
            </form>
          ) : null}
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
