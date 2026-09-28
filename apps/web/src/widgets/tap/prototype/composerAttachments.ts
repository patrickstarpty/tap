import type { LibrarySource } from "./model";

// Matches the file types Library "Add source" accepts.
export const ATTACHMENT_EXTENSIONS = ["pdf", "docx", "md", "txt"] as const;
export const ATTACHMENT_ACCEPT = ATTACHMENT_EXTENSIONS.map(
  (extension) => `.${extension}`,
).join(",");
export const ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;

export interface ComposerAttachment {
  conversationId: string;
  sourceId: string;
}

export type ComposerAttachmentStatus = "processing" | "failed" | "needs-review";

export interface ComposerAttachmentView {
  id: string;
  name: string;
  type: string;
  status: ComposerAttachmentStatus;
}

export function validateAttachmentFile(
  file: Pick<File, "name" | "size">,
): "unsupported" | "too-large" | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (
    !file.name.includes(".") ||
    !(ATTACHMENT_EXTENSIONS as readonly string[]).includes(extension)
  )
    return "unsupported";
  if (file.size > ATTACHMENT_MAX_BYTES) return "too-large";
  return null;
}

/**
 * Splits composer attachments into chips that still need Library processing or
 * review, and sources that are published and can join the message context.
 */
export function resolveComposerAttachments(
  attachments: readonly ComposerAttachment[],
  sources: readonly LibrarySource[],
): {
  pending: readonly (ComposerAttachmentView & { conversationId: string })[];
  ready: readonly ComposerAttachment[];
} {
  const pending: (ComposerAttachmentView & { conversationId: string })[] = [];
  const ready: ComposerAttachment[] = [];
  for (const attachment of attachments) {
    const source = sources.find((item) => item.id === attachment.sourceId);
    if (source === undefined) continue;
    if (source.status === "ready") {
      ready.push(attachment);
      continue;
    }
    pending.push({
      conversationId: attachment.conversationId,
      id: source.id,
      name: source.name,
      type: source.type,
      status:
        source.status === "failed"
          ? "failed"
          : source.reviewState === "processing"
            ? "processing"
            : "needs-review",
    });
  }
  return { pending, ready };
}
