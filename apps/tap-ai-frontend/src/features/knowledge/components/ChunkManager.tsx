import { readChunkCsv } from "../model/chunkCsv";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Input,
  Modal,
  Pagination,
  Radio,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  chunkPath,
  chunkRequest,
  type ChunkBatch,
  type ChunkPage,
  type ManagedChunk,
} from "../api/chunks";
import "./ChunkManager.css";

export function ChunkManager({
  projectId,
  documentId,
  fullDocument = false,
  parentChild = false,
}: {
  projectId: string;
  documentId: string;
  fullDocument?: boolean;
  parentChild?: boolean;
}) {
  const path = chunkPath(projectId, documentId);
  const creationKey = useRef(crypto.randomUUID());
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<ManagedChunk[]>([]);
  const [editing, setEditing] = useState<ManagedChunk | null>(null);
  const [editingChild, setEditingChild] = useState(false);
  const [creating, setCreating] = useState(false);
  const [parent, setParent] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [regenerate, setRegenerate] = useState(true);
  const [importing, setImporting] = useState(false);
  const [csvContents, setCsvContents] = useState<string[] | null>(null);
  const [csvReading, setCsvReading] = useState(false);
  const [deleting, setDeleting] = useState<ManagedChunk[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["chunks", projectId, documentId, q, status, page],
    queryFn: () =>
      chunkRequest<ChunkPage>(
        `${path}/chunks?${new URLSearchParams({ q, status, page: String(page), pageSize: "20" })}`,
      ),
    refetchInterval: 2000,
  });
  async function run(action: () => Promise<unknown>, done?: () => void) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await query.refetch();
      done?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  async function batch(action: string, items: ManagedChunk[]) {
    const result = await chunkRequest<ChunkBatch>(
      `${path}/chunks/batch`,
      "POST",
      {
        action,
        items: items.map(({ chunkId, version }) => ({ chunkId, version })),
      },
    );
    if (result.failed.length) {
      setSelected(
        items.filter((c) => result.failed.some((f) => f.chunkId === c.chunkId)),
      );
      throw new Error(
        `${result.succeeded.length} 项完成，${result.failed.length} 项失败。请刷新后重试失败项。`,
      );
    }
    setSelected([]);
  }
  function openEdit(chunk: ManagedChunk, child: boolean) {
    setEditingChild(child);
    setEditing(chunk);
    setDraft(chunk.content);
    setRegenerate(true);
    setError(null);
  }
  function create(parentId: string | null = null) {
    creationKey.current = crypto.randomUUID();
    setParent(parentId);
    setDraft("");
    setCreating(true);
    setError(null);
  }
  const rows = (items: ManagedChunk[], child = false) =>
    items.map((chunk) => (
      <article className="tapper-chunk" key={chunk.chunkId}>
        <div className="tapper-chunk-heading">
          <Space wrap>
            <Checkbox
              aria-label={`选择切片 ${chunk.position}`}
              checked={selected.some((c) => c.chunkId === chunk.chunkId)}
              onChange={(e) =>
                setSelected(
                  e.target.checked
                    ? [...selected, chunk]
                    : selected.filter((c) => c.chunkId !== chunk.chunkId),
                )
              }
            />
            <strong>
              {child ? "子块" : "切片"} {chunk.position}
            </strong>
            <span>
              {chunk.charCount} 字符 · {chunk.tokens} tokens
            </span>
            {chunk.edited && <Tag>已编辑</Tag>}
            <Tag
              color={
                chunk.indexStatus === "error"
                  ? "error"
                  : chunk.indexStatus === "ready"
                    ? "success"
                    : "processing"
              }
            >
              {chunk.indexStatus === "ready"
                ? "索引就绪"
                : chunk.indexStatus === "error"
                  ? "索引失败"
                  : "索引处理中"}
            </Tag>
          </Space>
          <Switch
            aria-label={`启用切片 ${chunk.position}`}
            checked={chunk.enabled}
            disabled={busy}
            checkedChildren="启用"
            unCheckedChildren="禁用"
            onChange={(enabled) =>
              void run(() =>
                chunkRequest(
                  `${path}/chunks/${encodeURIComponent(chunk.chunkId)}`,
                  "PATCH",
                  { version: chunk.version, enabled },
                ),
              )
            }
          />
        </div>
        <p className="tapper-chunk-content">{chunk.content}</p>
        <Space wrap>
          <Button
            aria-label={`编辑切片 ${chunk.position}`}
            onClick={() => openEdit(chunk, child)}
          >
            查看 / 编辑
          </Button>
          <Button danger onClick={() => setDeleting([chunk])}>
            删除
          </Button>
          {chunk.indexStatus === "error" && (
            <Button
              disabled={busy}
              onClick={() => void run(() => batch("retry", [chunk]))}
            >
              重试索引
            </Button>
          )}
          {parentChild && !child && (
            <Button onClick={() => create(chunk.chunkId)}>添加子块</Button>
          )}
        </Space>
        {chunk.indexError && <p role="status">索引失败，请重试索引。</p>}
        {(chunk.children ?? []).length > 0 && (
          <details className="tapper-chunk-children">
            <summary>{(chunk.children ?? []).length} 个子块</summary>
            {rows(chunk.children ?? [], true)}
          </details>
        )}
      </article>
    ));
  return (
    <section aria-label="切片管理" className="tapper-chunk-manager">
      <Typography.Title level={4}>切片管理</Typography.Title>
      <Typography.Paragraph type="secondary">
        保存后更新索引。启用且索引就绪的切片可直接用于知识检索。
      </Typography.Paragraph>
      <div className="tapper-chunk-toolbar">
        <Input
          aria-label="搜索切片内容"
          placeholder="搜索切片内容"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <Radio.Group
          aria-label="切片状态"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as string);
            setPage(1);
          }}
          options={[
            { label: "全部", value: "all" },
            { label: "启用", value: "enabled" },
            { label: "禁用", value: "disabled" },
          ]}
        />
        <Button onClick={() => create()} disabled={fullDocument}>
          新增切片
        </Button>
        <Button
          onClick={() => {
            setDraft("");
            creationKey.current = crypto.randomUUID();
            setCsvContents(null);
            setImporting(true);
            setError(null);
          }}
          disabled={fullDocument}
        >
          批量导入
        </Button>
      </div>
      {selected.length > 0 && (
        <Space wrap>
          <span>已选择 {selected.length} 项</span>
          {[
            ["enable", "批量启用"],
            ["disable", "批量禁用"],
          ].map(([action, label]) => (
            <Button
              key={action}
              disabled={busy}
              onClick={() => void run(() => batch(action!, selected))}
            >
              {label}
            </Button>
          ))}
          <Button danger disabled={busy} onClick={() => setDeleting(selected)}>
            批量删除
          </Button>
          <Button onClick={() => setSelected([])}>取消选择</Button>
        </Space>
      )}
      {error && !editing && !creating && !importing && !deleting && (
        <Alert role="alert" type="error" title={error} />
      )}{" "}
      {query.isPending && <Spin />}
      {query.isError && (
        <Alert
          type="error"
          title="切片加载失败"
          action={<Button onClick={() => void query.refetch()}>重试</Button>}
        />
      )}
      {query.data && (
        <>
          <p>{query.data.total} 个切片</p>
          {query.data.items.length ? (
            rows(query.data.items)
          ) : (
            <Empty description="没有匹配的切片" />
          )}
          <Pagination
            current={page}
            total={query.data.total}
            pageSize={20}
            showSizeChanger={false}
            onChange={setPage}
          />
        </>
      )}
      <Modal
        open={editing !== null || creating || importing}
        title={importing ? "批量导入切片" : creating ? "新增切片" : "编辑切片"}
        onCancel={() => {
          if (!busy) {
            setEditing(null);
            setCreating(false);
            setImporting(false);
          }
        }}
        footer={
          <Space>
            <Button
              disabled={busy}
              onClick={() => {
                setEditing(null);
                setCreating(false);
                setImporting(false);
              }}
            >
              取消
            </Button>
            {!(fullDocument && editing && !editingChild) && (
              <Button
                type="primary"
                loading={busy}
                disabled={
                  csvReading ||
                  (importing && csvContents !== null
                    ? csvContents.length === 0
                    : !draft.trim())
                }
                onClick={() =>
                  void run(
                    async () => {
                      if (importing) {
                        const result = await chunkRequest<ChunkBatch>(
                          `${path}/chunks/import`,
                          "POST",
                          {
                            contents:
                              csvContents ??
                              draft
                                .split(/^---\s*$/m)
                                .map((s) => s.trim())
                                .filter(Boolean),
                          },
                          creationKey.current,
                        );
                        if (result.failed.length)
                          throw new Error(
                            `${result.failed.length} 项导入失败，请检查后重试。`,
                          );
                      } else if (editing)
                        await chunkRequest(
                          `${path}/chunks/${encodeURIComponent(editing.chunkId)}`,
                          "PATCH",
                          {
                            version: editing.version,
                            content: draft,
                            regenerateChildren: regenerate,
                          },
                        );
                      else
                        await chunkRequest(
                          `${path}/chunks${parent ? `/${encodeURIComponent(parent)}/children` : ""}`,
                          "POST",
                          { content: draft },
                          creationKey.current,
                        );
                    },
                    () => {
                      setEditing(null);
                      setCreating(false);
                      setImporting(false);
                    },
                  )
                }
              >
                保存并索引
              </Button>
            )}
          </Space>
        }
      >
        {importing && (
          <>
            <label>
              选择 CSV 文件
              <input
                type="file"
                accept=".csv,text/csv"
                aria-label="选择 CSV 文件"
                disabled={busy || csvReading}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  setCsvContents(null);
                  setDraft("");
                  setError(null);
                  creationKey.current = crypto.randomUUID();
                  if (!file) return;
                  setCsvReading(true);
                  void readChunkCsv(file)
                    .then((contents) => {
                      setCsvContents(contents);
                    })
                    .catch((failure: unknown) =>
                      setError(
                        failure instanceof Error
                          ? failure.message
                          : "CSV 读取失败，请重试。",
                      ),
                    )
                    .finally(() => setCsvReading(false));
                }}
              />
            </label>
            {csvContents && (
              <p role="status">已读取 {csvContents.length} 个切片</p>
            )}
            <p>
              CSV 必须包含 content 列。也可粘贴文本，以单独一行 ---
              分隔每个切片。
            </p>
          </>
        )}
        <Input.TextArea
          disabled={csvReading || (importing && csvContents !== null)}
          readOnly={fullDocument && editing !== null && !editingChild}
          aria-label="切片内容"
          value={draft}
          rows={12}
          onChange={(e) => {
            creationKey.current = crypto.randomUUID();
            setDraft(e.target.value);
          }}
        />
        <p>{Array.from(draft).length} 字符</p>
        {editing && parentChild && !editingChild && !fullDocument && (
          <Checkbox
            checked={regenerate}
            onChange={(e) => setRegenerate(e.target.checked)}
          >
            重新生成子块（取消则保留现有子块）
          </Checkbox>
        )}
        {error && <Alert type="error" title={error} />}
      </Modal>
      <Modal
        open={deleting !== null}
        title="删除切片"
        onCancel={() => !busy && setDeleting(null)}
        onOk={() =>
          void run(
            () => batch("delete", deleting ?? []),
            () => setDeleting(null),
          )
        }
        confirmLoading={busy}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        <p>删除 {deleting?.length} 个切片？删除后不会出现在新的检索结果中。</p>
        {error && <Alert type="error" title={error} />}
      </Modal>
    </section>
  );
}
