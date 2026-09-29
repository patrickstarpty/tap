import type { components } from "../../../shared/api/generated/schema";
export type ChunkSettings = Required<
  components["schemas"]["KnowledgeChunkSettings"]
>;
export type ManagedChunk = components["schemas"]["KnowledgeChunk"];
export type ChunkPage = components["schemas"]["KnowledgeChunkPage"];
export type ChunkBatch = components["schemas"]["KnowledgeChunkBatchResult"];
export const DEFAULT_CHUNK_SETTINGS: ChunkSettings = {
  mode: "general",
  parentMode: "paragraph",
  separator: "\n\n",
  maxLength: 1024,
  overlap: 50,
  childSeparator: "\n",
  childMaxLength: 256,
  replaceWhitespace: false,
  removeUrls: false,
};
export function chunkPath(projectId: string, documentId: string) {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/documents/${encodeURIComponent(documentId)}`;
}
export async function chunkRequest<T>(
  path: string,
  method = "GET",
  body?: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    ...(body === undefined
      ? {}
      : {
          headers: {
            "Content-Type": "application/json",
            ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
          },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok)
    throw new Error(
      response.status === 409
        ? "版本已变化，草稿已保留。请重新打开最新切片后合并修改。"
        : "操作未完成，请重试。你的输入已保留。",
    );
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
