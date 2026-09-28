import { KnowledgeReview } from "./KnowledgeReview";
import { useState } from "react";
import { chunkPath, type ChunkSettings } from "../api/chunks";
import { useDocumentDetailQuery } from "../api/queries";
import { ChunkManager } from "./ChunkManager";
import { DocumentChunkSettings } from "./ChunkSettings";
export function DocumentChunks({
  projectId,
  documentId,
}: {
  projectId: string;
  documentId: string;
}) {
  const [settings, setSettings] = useState<ChunkSettings | null>(null);
  const [originalRevisionId, setOriginalRevisionId] = useState<string | null>(
    null,
  );
  const detail = useDocumentDetailQuery(projectId, documentId);
  return (
    <div className="tapper-detail-stack">
      <a
        href={`${chunkPath(projectId, documentId)}/original`}
        target="_blank"
        rel="noreferrer"
      >
        查看原始文件
      </a>
      <DocumentChunkSettings
        projectId={projectId}
        documentId={documentId}
        onLoaded={(value, revisionId) => {
          setSettings(value);
          setOriginalRevisionId(revisionId);
        }}
      />
      <ChunkManager
        projectId={projectId}
        documentId={documentId}
        parentChild={settings?.mode === "parent_child"}
        fullDocument={
          settings?.mode === "parent_child" &&
          settings.parentMode === "full_doc"
        }
      />
      <details>
        <summary>查看当前规范化内容</summary>
        <pre className="tapper-preview">
          {detail.data?.normalizedPreview ?? "当前规范化内容暂不可用"}
        </pre>
      </details>
      {originalRevisionId && (
        <details>
          <summary>历史审核记录</summary>
          <KnowledgeReview
            readOnly
            documentId={documentId}
            sourceRevisionId={originalRevisionId}
          />
        </details>
      )}
    </div>
  );
}
