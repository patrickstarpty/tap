import { useRef, useState } from "react";
import { Alert, Button } from "antd";
import type { ChunkSettings, ManagedChunk } from "../api/chunks";
import { ChunkSettingsFields } from "./ChunkSettings";
import "./ChunkManager.css";
export function UploadChunkPreview({
  projectId,
  file,
  value,
  onChange,
}: {
  projectId: string;
  file: File;
  value: ChunkSettings;
  onChange: (v: ChunkSettings) => void;
}) {
  const [preview, setPreview] = useState<{
    items: ManagedChunk[];
    total: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  async function load() {
    const current = ++generation.current;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("upload", file);
    form.append("settings", JSON.stringify(value));
    try {
      const response = await fetch(
        `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/chunks/preview`,
        { method: "POST", body: form },
      );
      if (!response.ok)
        throw new Error("预览失败，请检查文件与切片参数后重试。");
      const result = (await response.json()) as {
        items: ManagedChunk[];
        total: number;
      };
      if (current === generation.current) setPreview(result);
    } catch (e) {
      if (current === generation.current)
        setError(e instanceof Error ? e.message : "预览失败，请重试。");
    } finally {
      if (current === generation.current) setBusy(false);
    }
  }
  return (
    <section className="tapper-chunk-settings" aria-label="上传切片配置">
      <h3>切片设置</h3>
      <ChunkSettingsFields
        value={value}
        onChange={(v) => {
          generation.current++;
          setBusy(false);
          setPreview(null);
          onChange(v);
        }}
      />
      <Button loading={busy} onClick={() => void load()}>
        预览切片
      </Button>
      {error && <Alert type="error" title={error} />}{" "}
      {preview && (
        <div>
          <p>
            预览 {preview.items.length} / {preview.total} 个切片
          </p>
          {preview.items.map((c, i) => (
            <div key={i}>
              <pre className="tapper-preview">{c.content}</pre>
              {(c.children ?? []).length > 0 && (
                <details>
                  <summary>{(c.children ?? []).length} 个子块</summary>
                  {(c.children ?? []).map((child, n) => (
                    <pre className="tapper-preview" key={n}>
                      {child.content}
                    </pre>
                  ))}
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
