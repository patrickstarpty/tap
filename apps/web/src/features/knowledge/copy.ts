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
  sourcesTitle: "来源",
  sourcesDescription: "选择本次回答可以使用的已就绪文档。",
  selectedSources: (count: number) => `已选择 ${count} 个来源`,
  searchSources: "搜索来源",
  selectAllReady: "选择全部已就绪来源",
  clearSelection: "清除选择",
  sourceLimit: "一次最多选择 20 个来源。",
  sourcesLoading: "正在加载来源",
  sourcesEmpty: "还没有可用来源",
  sourcesEmptyDescription: "请先在知识库添加文档并等待处理完成。",
  sourcesFailure: "暂时无法加载来源，请稍后重试。",
  questionTitle: "问答",
  questionDescription: "Athena 只依据当前选择的来源组织可核验回答。",
  questionLabel: "输入问题",
  questionPlaceholder: "输入一个关于所选来源的问题",
  ask: "提问",
  queryRequired: "请输入问题。",
  queryTooLong: "问题最多包含 8,000 个字符。",
  pendingSearch: "检索所选来源",
  pendingAnswer: "组织可核验回答",
  answerEmpty: "选择来源并提问后，回答会显示在这里。",
  citationTitle: "原文",
  citationEmpty: "选择回答中的引用以核验原文。",
  citationEvidence: "原文依据",
  citationLoading: "正在核验原文",
  closeCitation: "关闭原文",
  retryCitation: "重新核验",
  citationStale: "引用已失效，来源可能已经变化，请重新提交问题。",
  citationUnavailable: "原文暂时无法核验，请稍后重试。",
  citationInvalid: "原文校验失败，请重新提交问题。",
  citationGenericFailure: "原文核验未完成，请稍后重试。",
  sourceContentHash: "Source content SHA-256",
  chunkContentHash: "Chunk content SHA-256",
  headingPath: "标题路径",
  page: "页码",
  offsets: "字符范围",
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

const ANSWER_PROBLEM_COPY: Readonly<Record<string, string>> = {
  "400:source-selection-required": "请选择 1 到 20 个已就绪来源。",
  "400:unsupported-answer-control": "当前问答请求包含不受支持的设置。",
  "409:document-state-changed": "所选来源状态已经变化，请确认来源后重新提问。",
  "422:request-validation": "问题或来源选择不符合要求，请检查后重试。",
  "503:embedding-unavailable": "向量服务暂时不可用，请稍后重试。",
  "503:answer-unavailable": "回答模型暂时不可用，请稍后重试。",
  "503:search-unavailable": "检索服务暂时不可用，请稍后重试。",
  "503:answer-snapshot-unavailable": "回答暂时无法安全保存，请稍后重试。",
  "503:knowledge-runtime-unavailable": "知识服务暂时不可用，请稍后重试。",
};

export function safeAnswerProblemCopy(error: unknown): string {
  if (error instanceof KnowledgeClientError) {
    const copy = ANSWER_PROBLEM_COPY[`${error.status}:${error.code}`];
    if (copy !== undefined) return copy;
  }
  return "回答未完成，请稍后重试。";
}

export type CitationProblemKind = "stale" | "retryable" | "generic";

export function safeCitationProblem(error: unknown): {
  kind: CitationProblemKind;
  message: string;
} {
  if (error instanceof KnowledgeClientError) {
    if (error.status === 404 && error.code === "citation-stale") {
      return { kind: "stale", message: COPY.citationStale };
    }
    if (
      error.status === 503 &&
      (error.code === "citation-unavailable" ||
        error.code === "knowledge-runtime-unavailable")
    ) {
      return { kind: "retryable", message: COPY.citationUnavailable };
    }
  }
  return { kind: "generic", message: COPY.citationGenericFailure };
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
