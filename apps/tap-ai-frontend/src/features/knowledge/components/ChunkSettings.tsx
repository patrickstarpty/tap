import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Checkbox,
  Input,
  InputNumber,
  Modal,
  Radio,
  Space,
} from "antd";
import {
  chunkPath,
  chunkRequest,
  DEFAULT_CHUNK_SETTINGS,
  type ChunkSettings as Settings,
  type ManagedChunk,
} from "../api/chunks";

export function ChunkSettingsFields({
  value,
  onChange,
  modeLocked = false,
}: {
  value: Settings;
  onChange: (value: Settings) => void;
  modeLocked?: boolean;
}) {
  const update = (patch: Partial<Settings>) => onChange({ ...value, ...patch });
  return (
    <div className="tapper-chunk-settings">
      <label>
        切片模式
        <Radio.Group
          value={value.mode}
          disabled={modeLocked}
          onChange={(e) => update({ mode: e.target.value as Settings["mode"] })}
          options={[
            { label: "通用", value: "general" },
            { label: "父子", value: "parent_child" },
          ]}
        />
      </label>
      {value.mode === "parent_child" && (
        <label>
          父块模式
          <Radio.Group
            value={value.parentMode}
            onChange={(e) =>
              update({ parentMode: e.target.value as Settings["parentMode"] })
            }
            options={[
              { label: "段落", value: "paragraph" },
              { label: "整份文档", value: "full_doc" },
            ]}
          />
        </label>
      )}
      <label>
        分隔符
        <Input
          aria-label="分隔符"
          value={value.separator.replaceAll("\n", "\\n")}
          onChange={(e) =>
            update({ separator: e.target.value.replaceAll("\\n", "\n") })
          }
        />
      </label>
      <Space wrap>
        <label>
          最大字符长度
          <InputNumber
            aria-label="最大字符长度"
            min={1}
            max={32768}
            value={value.maxLength}
            onChange={(n) => update({ maxLength: n ?? 1024 })}
          />
        </label>
        <label>
          重叠字符数
          <InputNumber
            aria-label="重叠字符数"
            min={0}
            max={value.maxLength - 1}
            value={value.overlap}
            onChange={(n) => update({ overlap: n ?? 0 })}
          />
        </label>
      </Space>
      {value.mode === "parent_child" && (
        <>
          <label>
            子块分隔符
            <Input
              aria-label="子块分隔符"
              value={value.childSeparator.replaceAll("\n", "\\n")}
              onChange={(e) =>
                update({
                  childSeparator: e.target.value.replaceAll("\\n", "\n"),
                })
              }
            />
          </label>
          <label>
            子块最大字符长度
            <InputNumber
              aria-label="子块最大字符长度"
              min={1}
              max={32768}
              value={value.childMaxLength}
              onChange={(n) => update({ childMaxLength: n ?? 256 })}
            />
          </label>
        </>
      )}
      <Checkbox
        checked={value.replaceWhitespace}
        onChange={(e) => update({ replaceWhitespace: e.target.checked })}
      >
        合并连续空白
      </Checkbox>
      <Checkbox
        checked={value.removeUrls}
        onChange={(e) => update({ removeUrls: e.target.checked })}
      >
        移除 URL 和电子邮件地址
      </Checkbox>
    </div>
  );
}
export function DocumentChunkSettings({
  projectId,
  documentId,
  onLoaded,
}: {
  projectId: string;
  documentId: string;
  onLoaded?: (settings: Settings, originalRevisionId: string) => void;
}) {
  const path = chunkPath(projectId, documentId);
  const cache = useQueryClient();
  const [draft, setDraft] = useState<Settings | null>(null);
  const [preview, setPreview] = useState<{
    items: ManagedChunk[];
    total: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const query = useQuery({
    queryKey: ["chunk-settings", projectId, documentId],
    queryFn: async () => {
      const result = await chunkRequest<{
        settings: Settings;
        version: number;
        originalRevisionId: string;
      }>(`${path}/chunk-settings`);
      onLoaded?.(result.settings, result.originalRevisionId);
      return result;
    },
  });
  const settings = draft ?? query.data?.settings ?? DEFAULT_CHUNK_SETTINGS;
  async function execute(save = false) {
    setBusy(true);
    setError(null);
    try {
      if (save) {
        await chunkRequest(`${path}/chunk-settings`, "PUT", {
          settings,
          version: query.data?.version,
          confirmReplace: true,
        });
        setConfirm(false);
        setDraft(null);
        setPreview(null);
        await query.refetch();
        await cache.invalidateQueries({
          queryKey: ["chunks", projectId, documentId],
        });
      } else
        setPreview(
          await chunkRequest(`${path}/chunks/preview`, "POST", { settings }),
        );
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>切片设置与预览</summary>
      <div className="tapper-chunk-settings">
        <ChunkSettingsFields
          value={settings}
          modeLocked
          onChange={(value) => {
            setDraft(value);
            setPreview(null);
          }}
        />
        {query.isError && (
          <Alert
            type="error"
            title="无法加载切片设置"
            action={<Button onClick={() => void query.refetch()}>重试</Button>}
          />
        )}
        <Space>
          <Button
            loading={busy}
            disabled={!query.data}
            onClick={() => void execute()}
          >
            预览切片
          </Button>
          <Button
            disabled={!query.data || busy}
            onClick={() => setConfirm(true)}
          >
            保存并重新处理
          </Button>
        </Space>
        {error && <Alert type="error" title={error} />}{" "}
        {preview && (
          <div>
            <p>
              预览 {preview.items.length} / {preview.total} 个切片
            </p>
            {preview.items.map((c, i) => (
              <pre className="tapper-preview" key={i}>
                {c.content}
                {(c.children ?? [])
                  .map((child) => `\n\n子块：${child.content}`)
                  .join("")}
              </pre>
            ))}
          </div>
        )}
      </div>
      <Modal
        open={confirm}
        title="重新处理文档"
        okText="确认替换并索引"
        cancelText="取消"
        confirmLoading={busy}
        onCancel={() => !busy && setConfirm(false)}
        onOk={() => void execute(true)}
      >
        <p>
          重新处理会替换当前切片及人工调整。旧版本保留供追溯，当前编辑不会自动合并。
        </p>
        {error && <Alert type="error" title={error} />}
      </Modal>
    </details>
  );
}
