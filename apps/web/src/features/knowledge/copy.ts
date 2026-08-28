import { KnowledgeClientError } from "./api/client";
import type { DocumentStatus, IngestionStage } from "./api/types";

export const COPY = {
  appName: "Athena",
  workspaceName: "Athena Lab",
  askTab: "问答",
  libraryTab: "知识库",
  askTitle: "从已就绪的来源开始提问",
  askDescription:
    "完整的来源选择、回答与引用核验将在下一阶段接入。这里不会生成模拟回答。",
  libraryTitle: "知识库",
  libraryDescription: "管理 Athena 可以引用的本地来源与处理状态。",
  addSource: "添加来源",
  emptyTitle: "还没有来源",
  emptyDescription: "添加一份文档，处理完成后即可用于基于来源的问答。",
  readyCount: "已就绪",
  processingCount: "处理中",
  failedCount: "失败",
  filenameColumn: "文件名",
  typeColumn: "类型",
  statusColumn: "状态 / 阶段",
  chunksColumn: "Chunks",
  updatedColumn: "更新时间",
  actionsColumn: "操作",
  viewDetail: "查看详情",
  retry: "重试",
  delete: "删除",
  close: "关闭",
  uploadTitle: "添加来源",
  dropTitle: "拖放文档到这里",
  dropDescription: "或按 Enter 选择文件 · 单次添加一份文档",
  acceptedFormats: "PDF、DOCX、Markdown 或 TXT · 最大 25 MiB",
  chooseDocument: "选择文档",
  dropZoneLabel: "拖放或选择文档",
  selectedFile: "已选择",
  startUpload: "开始添加",
  cancel: "取消",
  uploadProgress: (percentage: number) => `上传 ${percentage}%`,
  invalidFormat: "支持 PDF、DOCX、Markdown 和 TXT 文件。",
  oversizedFile: "文件超过 25 MiB，请选择更小的文档。",
  duplicateReceipt: "这个内容已经在知识库中，已显示现有来源。",
  uploadAccepted: "来源已接收，后台处理会继续进行。",
  failedStage: (stage: string) => `失败阶段：${stage}`,
  detailTitle: (filename: string) => `${filename} 详情`,
  immutableFacts: "来源事实",
  revisionId: "Revision ID",
  sourceHash: "Content SHA-256",
  ingestionTimeline: "处理阶段",
  normalizedPreview: "规范化预览",
  previewUnavailable: "暂时没有可显示的规范化预览。",
  deleteTitle: "删除来源",
  deleteConfirm: (filename: string) =>
    `确认删除“${filename}”？删除开始后，这份来源将立即不可用于选择。`,
  confirmDelete: "确认删除",
  loadFailure: "暂时无法加载知识库，请稍后重试。",
  detailFailure: "暂时无法加载来源详情，请稍后重试。",
  actionFailure: "操作未完成，请稍后重试。",
  uploadFailure: "文档未能添加，请检查文件后重试。",
} as const;

export const STATUS_COPY: Readonly<Record<DocumentStatus, string>> = {
  queued: "等待处理",
  processing: "处理中",
  ready: "已就绪",
  failed: "处理失败",
  deleting: "正在删除",
};

export const STAGE_COPY: Readonly<Record<IngestionStage, string>> = {
  stored: "已保存源文件",
  parsing: "正在解析内容",
  chunking: "正在整理片段",
  embedding: "正在生成向量",
  publishing: "正在发布索引",
  ready: "已可用于问答",
};

export const STAGE_TITLES: Readonly<Record<IngestionStage, string>> = {
  stored: "保存源文件",
  parsing: "解析内容",
  chunking: "整理片段",
  embedding: "生成向量",
  publishing: "发布索引",
  ready: "可用于问答",
};

export const STAGE_STATE_COPY = {
  pending: "等待中",
  processing: "进行中",
  completed: "已完成",
  failed: "失败",
} as const;

const PROBLEM_COPY: Readonly<Record<string, string>> = {
  "document-too-large": "文件超过服务端允许的大小，请选择更小的文档。",
  "unsupported-document": COPY.invalidFormat,
  "empty-document": "文档没有可处理的内容，请选择非空文件。",
  "ocr-required": "这份文档没有可提取文本，当前版本不支持 OCR。",
  "document-limit-reached":
    "知识库已达到 50 份文档上限，请先删除不再需要的来源。",
};

export function safeProblemCopy(
  error: unknown,
  fallback: "list" | "detail" | "upload" | "action",
): string {
  if (error instanceof KnowledgeClientError) {
    const allowlisted = PROBLEM_COPY[error.code];
    if (allowlisted !== undefined) return allowlisted;
  }
  if (fallback === "list") return COPY.loadFailure;
  if (fallback === "detail") return COPY.detailFailure;
  if (fallback === "upload") return COPY.uploadFailure;
  return COPY.actionFailure;
}

export function mediaTypeCopy(mediaType: string): string {
  if (mediaType === "application/pdf") return "PDF";
  if (
    mediaType ===
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  ) {
    return "DOCX";
  }
  if (mediaType === "text/markdown") return "Markdown";
  return "TXT";
}
