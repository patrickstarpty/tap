import { useEffect, useState, type ReactNode } from "react";
import { Button } from "antd";

export type Chunk = {
  id: string;
  content: string;
  enabled: boolean;
  edited: boolean;
  children: Chunk[];
};
export type ChunkSettings = {
  mode: "general" | "parent-child";
  parent: "paragraph" | "full-doc";
  delimiter: string;
  max: number;
  overlap: number;
  childDelimiter: string;
  childMax: number;
  whitespace: boolean;
  removeLinks: boolean;
};
export const DEFAULT_CHUNK_SETTINGS: ChunkSettings = {
  mode: "general",
  parent: "paragraph",
  delimiter: "\\n\\n",
  max: 500,
  overlap: 50,
  childDelimiter: "\\n",
  childMax: 200,
  whitespace: true,
  removeLinks: false,
};
export const SAMPLE_CHUNK_SOURCE_TEXT =
  "The application records the applicant information and supporting disclosures. Check the applicable version and scope before using this guide.\n\nAn application must include a completed health disclosure before submission. If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED. Keep entered information and allow the applicant to complete missing fields before resubmitting.\n\nReview the application and completed disclosure together before resubmitting. Previously entered information remains available while the applicant completes missing fields.";
const makeChunk = (content: string): Chunk => ({
  id: crypto.randomUUID(),
  content,
  enabled: true,
  edited: false,
  children: [],
});
export function splitChunkText(
  text: string,
  delimiter: string,
  max: number,
  overlap = 0,
): string[] {
  const size = Math.max(1, Math.floor(max));
  const step = Math.max(1, size - Math.min(size - 1, Math.max(0, overlap)));
  return text
    .split(delimiter.replaceAll("\\n", "\n") || "\n\n")
    .flatMap((part) => {
      const result: string[] = [];
      for (let start = 0; start < part.length; start += step) {
        const value = part.slice(start, start + size).trim();
        if (value) result.push(value);
        if (start + size >= part.length) break;
      }
      return result;
    });
}
export function generateChunks(text: string, s: ChunkSettings): Chunk[] {
  const clean = s.removeLinks
    ? text.replace(/https?:\/\/\S+|[\w.+-]+@[\w.-]+\.[a-z]+/gi, "")
    : text;
  const normalized = s.whitespace
    ? clean.replace(/[^\S\n]+/g, " ").replace(/\n{3,}/g, "\n\n")
    : clean;
  const parents =
    s.mode === "parent-child" && s.parent === "full-doc"
      ? [normalized]
      : splitChunkText(
          normalized,
          s.delimiter,
          s.max,
          s.mode === "general" ? s.overlap : 0,
        );
  return parents.map((content) => ({
    ...makeChunk(content),
    children:
      s.mode === "parent-child"
        ? splitChunkText(content, s.childDelimiter, s.childMax).map(makeChunk)
        : [],
  }));
}
export function parseChunkCsv(text: string): string[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') {
        field += '"';
        i++;
      } else quoted = !quoted;
    } else if (c === "," && !quoted) {
      row.push(field);
      field = "";
    } else if ((c === "\n" || c === "\r") && !quoted) {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (quoted) throw new Error("Unclosed quoted CSV field");
  row.push(field);
  rows.push(row);
  const column =
    rows[0]?.findIndex((x) => x.trim().toLowerCase() === "content") ?? -1;
  if (column < 0) throw new Error("CSV must contain a content column");
  return rows
    .slice(1)
    .map((r) => r[column]?.trim() ?? "")
    .filter(Boolean);
}
export function ChunkManager({
  id,
  t,
  originalView,
  onAvailability,
  initialSettings = false,
}: {
  id: string;
  t: (en: string, zh: string) => string;
  originalView: ReactNode;
  onAvailability: (ready: boolean) => void;
  initialSettings?: boolean;
}) {
  const key = `tap.prototype.chunks.v1.${id}`;
  const [saved, setSaved] = useState<{
    settings: ChunkSettings;
    chunks: Chunk[];
    versions?: Chunk[][];
  }>(() => {
    try {
      const value = JSON.parse(localStorage.getItem(key) ?? "null");
      if (value && Array.isArray(value.chunks) && value.settings) return value;
    } catch {
      /* use source */
    }
    return {
      settings: DEFAULT_CHUNK_SETTINGS,
      chunks: generateChunks(SAMPLE_CHUNK_SOURCE_TEXT, DEFAULT_CHUNK_SETTINGS),
    };
  });
  const [settings, setSettings] = useState(saved.settings);
  const [tab, setTab] = useState(initialSettings ? "settings" : "chunks");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [editor, setEditor] = useState<{
    id?: string;
    parent?: string;
    content: string;
  } | null>(null);
  const [regenerate, setRegenerate] = useState(true);
  const [preview, setPreview] = useState<Chunk[] | null>(null);
  const [confirm, setConfirm] = useState<"delete" | "reprocess" | null>(null);
  const [notice, setNotice] = useState("");
  const [indexing, setIndexing] = useState(false);
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(saved));
    } catch {
      /* session state remains */
    }
    onAvailability(
      !indexing &&
        (!initialSettings || Boolean(saved.versions?.length)) &&
        saved.chunks.some((c) => c.enabled),
    );
  }, [saved, indexing, key]);
  useEffect(() => {
    if (!indexing) return;
    const timer = setTimeout(() => {
      setIndexing(false);
      setNotice(
        t(
          "Index ready · enabled chunks are searchable",
          "索引就绪 · 已启用切片可检索",
        ),
      );
    }, 700);
    return () => clearTimeout(timer);
  }, [indexing, t]);
  const commit = (chunks: Chunk[]) => {
    setSaved((s) => ({ ...s, chunks }));
    setIndexing(true);
    setNotice(t("Saved · updating index…", "已保存 · 正在更新索引…"));
  };
  const filtered = saved.chunks.filter(
    (c) =>
      (status === "all" || c.enabled === (status === "enabled")) &&
      `${c.content} ${c.children.map((x) => x.content).join(" ")}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 5));
  const currentPage = Math.min(page, pages - 1);
  const visible = filtered.slice(currentPage * 5, currentPage * 5 + 5);
  const full =
    saved.settings.mode === "parent-child" &&
    saved.settings.parent === "full-doc";
  const patchSetting = <K extends keyof ChunkSettings>(
    key: K,
    value: ChunkSettings[K],
  ) => {
    setSettings((s) => ({ ...s, [key]: value }));
    setPreview(null);
  };
  const toggle = (ids: string[], enabled: boolean) => {
    commit(
      saved.chunks.map((c) => (ids.includes(c.id) ? { ...c, enabled } : c)),
    );
    setSelected([]);
  };
  const requestDelete = (ids: string[]) => {
    setSelected(ids);
    setConfirm("delete");
  };
  const save = () => {
    if (!editor?.content.trim()) return;
    const change = (chunks: Chunk[]) =>
      editor.id
        ? chunks.map((c) =>
            c.id === editor.id
              ? {
                  ...c,
                  content: editor.content.trim(),
                  edited: true,
                  children:
                    !editor.parent &&
                    saved.settings.mode === "parent-child" &&
                    regenerate
                      ? splitChunkText(
                          editor.content,
                          settings.childDelimiter,
                          settings.childMax,
                        ).map(makeChunk)
                      : c.children,
                }
              : c,
          )
        : [
            ...chunks,
            {
              ...makeChunk(editor.content.trim()),
              edited: true,
              children:
                !editor.parent && saved.settings.mode === "parent-child"
                  ? splitChunkText(
                      editor.content,
                      settings.childDelimiter,
                      settings.childMax,
                    ).map(makeChunk)
                  : [],
            },
          ];
    commit(
      editor.parent
        ? saved.chunks.map((c) =>
            c.id === editor.parent ? { ...c, children: change(c.children) } : c,
          )
        : change(saved.chunks),
    );
    setEditor(null);
  };
  return (
    <div className="tap-chunk-manager">
      <nav
        aria-label={t("Document views", "文档视图")}
        className="tap-chunk-tabs"
      >
        {(["chunks", "settings", "original"] as const).map((value) => (
          <Button
            key={value}
            type={tab === value ? "primary" : "text"}
            onClick={() => setTab(value)}
          >
            {value === "chunks"
              ? t("Chunks", "切片")
              : value === "settings"
                ? t("Chunk settings", "切片设置")
                : t("Original document", "原始文档")}
          </Button>
        ))}
      </nav>
      <p role="status" className="tap-chunk-notice">
        {notice ||
          t(
            "Enabled chunks are available to Tapper when indexing completes.",
            "索引完成后，Tapper 可检索已启用的切片。",
          )}
      </p>
      {tab === "original" ? (
        originalView
      ) : tab === "settings" ? (
        <div className="tap-chunk-settings">
          <div>
            <label>
              {t("Chunk mode", "切片模式")}
              <select
                value={settings.mode}
                disabled={!initialSettings || Boolean(saved.versions?.length)}
                onChange={(e) =>
                  patchSetting("mode", e.target.value as ChunkSettings["mode"])
                }
              >
                <option value="general">{t("General", "通用")}</option>
                <option value="parent-child">
                  {t("Parent-child", "父子")}
                </option>
              </select>
            </label>
            {(!initialSettings || Boolean(saved.versions?.length)) && (
              <small>
                {t(
                  "The collection chunk mode is fixed.",
                  "知识集合的切片模式已固定。",
                )}
              </small>
            )}
            {settings.mode === "parent-child" && (
              <label>
                {t("Parent context", "父块上下文")}
                <select
                  value={settings.parent}
                  onChange={(e) =>
                    patchSetting("parent", e.target.value as ChunkSettings["parent"])
                  }
                >
                  <option value="paragraph">{t("Paragraph", "段落")}</option>
                  <option value="full-doc">
                    {t("Full document", "整份文档")}
                  </option>
                </select>
              </label>
            )}
            {!(
              settings.mode === "parent-child" && settings.parent === "full-doc"
            ) && (
              <>
                <label>
                  {t("Delimiter", "分隔符")}
                  <input
                    value={settings.delimiter}
                    onChange={(e) => patchSetting("delimiter", e.target.value)}
                  />
                </label>
                <label>
                  {t("Maximum characters", "最大字符数")}
                  <input
                    type="number"
                    min="1"
                    max="10000"
                    value={settings.max}
                    onChange={(e) =>
                      patchSetting("max", Number(e.target.value))
                    }
                  />
                </label>
              </>
            )}
            {settings.mode === "general" ? (
              <label>
                {t("Overlap characters", "重叠字符数")}
                <input
                  type="number"
                  min="0"
                  max={settings.max - 1}
                  value={settings.overlap}
                  onChange={(e) =>
                    patchSetting("overlap", Number(e.target.value))
                  }
                />
              </label>
            ) : (
              <>
                <label>
                  {t("Child delimiter", "子块分隔符")}
                  <input
                    value={settings.childDelimiter}
                    onChange={(e) =>
                      patchSetting("childDelimiter", e.target.value)
                    }
                  />
                </label>
                <label>
                  {t("Child maximum characters", "子块最大字符数")}
                  <input
                    type="number"
                    min="1"
                    max="10000"
                    value={settings.childMax}
                    onChange={(e) =>
                      patchSetting("childMax", Number(e.target.value))
                    }
                  />
                </label>
              </>
            )}
            <label>
              <input
                type="checkbox"
                checked={settings.whitespace}
                onChange={(e) => patchSetting("whitespace", e.target.checked)}
              />
              {t("Merge consecutive whitespace", "合并连续空白")}
            </label>
            <label>
              <input
                type="checkbox"
                checked={settings.removeLinks}
                onChange={(e) => patchSetting("removeLinks", e.target.checked)}
              />
              {t("Remove URLs and email addresses", "移除网址和电子邮件地址")}
            </label>
            <Button
              disabled={
                settings.max < 1 ||
                settings.childMax < 1 ||
                settings.overlap < 0 ||
                settings.overlap >= settings.max
              }
              onClick={() => setPreview(generateChunks(SAMPLE_CHUNK_SOURCE_TEXT, settings))}
            >
              {t("Preview chunks", "预览切片")}
            </Button>{" "}
            <Button
              type="primary"
              disabled={!preview || indexing}
              onClick={() => setConfirm("reprocess")}
            >
              {t("Save and process", "保存并处理")}
            </Button>
          </div>
          <section aria-label={t("Chunk preview", "切片预览")}>
            <h3>{t("Preview", "预览")}</h3>
            {preview ? (
              <>
                <p>
                  {t(
                    `Showing ${Math.min(3, preview.length)} of ${preview.length} chunks`,
                    `显示 ${Math.min(3, preview.length)} / ${preview.length} 个切片`,
                  )}
                </p>
                {preview.slice(0, 3).map((c, i) => (
                  <article key={c.id}>
                    <strong>
                      #{i + 1} · {c.content.length} {t("characters", "字符")}
                    </strong>
                    <p>{c.content}</p>
                    {c.children.map((child) => (
                      <blockquote key={child.id}>{child.content}</blockquote>
                    ))}
                  </article>
                ))}
              </>
            ) : (
              <p>
                {t(
                  "Adjust the rules, then preview the chunk boundaries.",
                  "调整规则后预览切片边界。",
                )}
              </p>
            )}
          </section>
        </div>
      ) : (
        <>
          <div className="tap-chunk-toolbar">
            <input
              type="search"
              aria-label={t("Search chunks", "搜索切片")}
              placeholder={t("Search chunk content", "搜索切片内容")}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
            <select
              aria-label={t("Chunk status", "切片状态")}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(0);
              }}
            >
              <option value="all">{t("All statuses", "全部状态")}</option>
              <option value="enabled">{t("Enabled", "已启用")}</option>
              <option value="disabled">{t("Disabled", "已禁用")}</option>
            </select>
            <Button disabled={full} onClick={() => setEditor({ content: "" })}>
              {t("Add chunk", "新增切片")}
            </Button>
            <label className="tap-chunk-import">
              {t("Import CSV", "导入 CSV")}
              <input
                aria-label={t("Import CSV", "导入 CSV")}
                type="file"
                accept=".csv"
                disabled={full}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    const contents = parseChunkCsv(await file.text());
                    if (!contents.length)
                      throw new Error(
                        t("CSV contains no content", "CSV 中没有内容"),
                      );
                    commit([
                      ...saved.chunks,
                      ...contents.map((content) => ({
                        ...makeChunk(content),
                        edited: true,
                        children:
                          saved.settings.mode === "parent-child"
                            ? splitChunkText(
                                content,
                                settings.childDelimiter,
                                settings.childMax,
                              ).map(makeChunk)
                            : [],
                      })),
                    ]);
                  } catch (error) {
                    setNotice(
                      error instanceof Error ? error.message : String(error),
                    );
                  }
                  e.target.value = "";
                }}
              />
            </label>
          </div>
          <div className="tap-chunk-toolbar">
            <label>
              <input
                type="checkbox"
                checked={
                  visible.length > 0 &&
                  visible.every((c) => selected.includes(c.id))
                }
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...new Set([...selected, ...visible.map((c) => c.id)])]
                      : selected.filter(
                          (id) => !visible.some((c) => c.id === id),
                        ),
                  )
                }
              />
              {t("Select page", "选择本页")}
            </label>
            <span>
              {filtered.length} {t("chunks", "个切片")} · {selected.length}{" "}
              {t("selected", "已选")}
            </span>
            {selected.length > 0 && (
              <>
                <Button onClick={() => toggle(selected, true)}>
                  {t("Enable selected", "启用所选")}
                </Button>
                <Button onClick={() => toggle(selected, false)}>
                  {t("Disable selected", "禁用所选")}
                </Button>
                <Button danger onClick={() => setConfirm("delete")}>
                  {t("Delete selected", "删除所选")}
                </Button>
              </>
            )}
          </div>
          <section aria-label={t("Chunks", "切片")} className="tap-chunk-list">
            {visible.map((c, i) => (
              <article key={c.id}>
                <div className="tap-chunk-row">
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`${t("Select chunk", "选择切片")} ${currentPage * 5 + i + 1}`}
                      checked={selected.includes(c.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, c.id]
                            : selected.filter((id) => id !== c.id),
                        )
                      }
                    />{" "}
                    #{currentPage * 5 + i + 1}
                  </label>
                  <small>
                    {c.content.length} {t("characters", "字符")} ·{" "}
                    {c.enabled
                      ? t("Enabled", "已启用")
                      : t("Disabled", "已禁用")}
                    {c.edited ? ` · ${t("Edited", "已编辑")}` : ""}
                  </small>
                </div>
                <p>{c.content}</p>
                <div className="tap-chunk-row">
                  <Button
                    aria-label={t("Edit chunk", "编辑切片")}
                    disabled={!c.enabled || full}
                    onClick={() => {
                      setEditor({ id: c.id, content: c.content });
                      setRegenerate(true);
                    }}
                  >
                    {t("Edit", "编辑")}
                  </Button>
                  <Button
                    aria-label={
                      c.enabled
                        ? t("Disable chunk", "禁用切片")
                        : t("Enable chunk", "启用切片")
                    }
                    onClick={() => toggle([c.id], !c.enabled)}
                  >
                    {c.enabled ? t("Disable", "禁用") : t("Enable", "启用")}
                  </Button>
                  <Button
                    danger
                    aria-label={t("Delete chunk", "删除切片")}
                    onClick={() => requestDelete([c.id])}
                  >
                    {t("Delete", "删除")}
                  </Button>
                  {full && (
                    <small>
                      {t(
                        "Full document parent is read-only. Replace the original to change it.",
                        "整份文档父块只读。请替换原文件以修改内容。",
                      )}
                    </small>
                  )}
                </div>
                {saved.settings.mode === "parent-child" && (
                  <details>
                    <summary>
                      {c.children.length} {t("child chunks", "个子块")}
                    </summary>
                    {c.children.map((child) => (
                      <div className="tap-chunk-child" key={child.id}>
                        <p>{child.content}</p>
                        <small>
                          {child.edited ? t("Edited", "已编辑") : ""}
                        </small>
                        <Button
                          disabled={!c.enabled || !child.enabled}
                          onClick={() =>
                            setEditor({
                              id: child.id,
                              parent: c.id,
                              content: child.content,
                            })
                          }
                        >
                          {t("Edit child", "编辑子块")}
                        </Button>
                        <Button
                          disabled={!c.enabled}
                          onClick={() =>
                            commit(
                              saved.chunks.map((p) =>
                                p.id === c.id
                                  ? {
                                      ...p,
                                      children: p.children.map((x) =>
                                        x.id === child.id
                                          ? { ...x, enabled: !x.enabled }
                                          : x,
                                      ),
                                    }
                                  : p,
                              ),
                            )
                          }
                        >
                          {child.enabled
                            ? t("Disable child", "禁用子块")
                            : t("Enable child", "启用子块")}
                        </Button>
                        <Button
                          danger
                          disabled={!c.enabled}
                          onClick={() => {
                            setEditor({
                              id: child.id,
                              parent: c.id,
                              content: child.content,
                            });
                            setConfirm("delete");
                          }}
                        >
                          {t("Delete child", "删除子块")}
                        </Button>
                      </div>
                    ))}
                    <Button
                      disabled={!c.enabled}
                      onClick={() => setEditor({ parent: c.id, content: "" })}
                    >
                      {t("Add child", "新增子块")}
                    </Button>
                  </details>
                )}
              </article>
            ))}
            {!visible.length && (
              <p>
                {t(
                  "No matching chunks. Change your filters or add a chunk.",
                  "没有匹配切片。请调整筛选条件或新增切片。",
                )}
              </p>
            )}
          </section>
          <div className="tap-chunk-toolbar">
            <Button
              disabled={currentPage === 0}
              onClick={() => setPage(currentPage - 1)}
            >
              {t("Previous page", "上一页")}
            </Button>
            <span>
              {currentPage + 1} / {pages}
            </span>
            <Button
              disabled={currentPage + 1 >= pages}
              onClick={() => setPage(currentPage + 1)}
            >
              {t("Next page", "下一页")}
            </Button>
          </div>
        </>
      )}
      {editor && !confirm && (
        <section
          className="tap-chunk-editor"
          aria-label={t("Chunk editor", "切片编辑器")}
        >
          <h3>
            {editor.parent ? t("Child chunk", "子块") : t("Chunk", "切片")}
          </h3>
          <label>
            {t("Chunk content", "切片内容")}
            <textarea
              rows={7}
              autoFocus
              value={editor.content}
              onChange={(e) =>
                setEditor({ ...editor, content: e.target.value })
              }
            />
          </label>
          {!editor.parent && settings.mode === "parent-child" && editor.id && (
            <label>
              <input
                type="checkbox"
                checked={regenerate}
                onChange={(e) => setRegenerate(e.target.checked)}
              />
              {t(
                "Regenerate child chunks from the updated parent",
                "根据修改后的父块重新生成子块",
              )}
            </label>
          )}
          <Button
            type="primary"
            disabled={!editor.content.trim()}
            onClick={save}
          >
            {t("Save chunk", "保存切片")}
          </Button>{" "}
          <Button onClick={() => setEditor(null)}>{t("Cancel", "取消")}</Button>
        </section>
      )}
      {confirm && (
        <section
          className="tap-chunk-editor"
          role="alertdialog"
          aria-label={t("Confirm change", "确认更改")}
        >
          <h3>
            {confirm === "delete"
              ? t("Delete selected content?", "删除所选内容？")
              : t(
                  "Replace chunks with the new preview?",
                  "使用新预览替换切片？",
                )}
          </h3>
          <p>
            {confirm === "delete"
              ? t(
                  "Deleted content will no longer be retrieved.",
                  "删除后将不再检索这些内容。",
                )
              : t(
                  "Processing replaces generated chunks and manual edits. The previous version is retained in document history.",
                  "重新处理将替换自动切片及人工修改。旧版本保留在文档历史中。",
                )}
          </p>
          <Button
            danger
            type="primary"
            onClick={() => {
              if (confirm === "reprocess" && preview) {
                setSaved((s) => ({
                  settings,
                  chunks: preview,
                  versions: [...(s.versions ?? []), s.chunks],
                }));
                setIndexing(true);
                setTab("chunks");
              } else if (editor?.parent) {
                commit(
                  saved.chunks.map((c) =>
                    c.id === editor.parent
                      ? {
                          ...c,
                          children: c.children.filter(
                            (x) => x.id !== editor.id,
                          ),
                        }
                      : c,
                  ),
                );
                setEditor(null);
              } else {
                commit(saved.chunks.filter((c) => !selected.includes(c.id)));
                setSelected([]);
              }
              setConfirm(null);
            }}
          >
            {confirm === "delete"
              ? t("Confirm delete", "确认删除")
              : t("Process document", "处理文档")}
          </Button>{" "}
          <Button
            onClick={() => {
              setConfirm(null);
              setEditor(null);
            }}
          >
            {t("Cancel", "取消")}
          </Button>
        </section>
      )}
    </div>
  );
}
