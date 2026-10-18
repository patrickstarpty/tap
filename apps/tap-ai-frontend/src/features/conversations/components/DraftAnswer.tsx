import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import { answerSchema } from "../../../shared/markdown/answerSchema";
import type { CitationLabelInfo, DraftState } from "../model/stream";

const DRAFT_COPY = {
  en: {
    checkingSources: "Checking sources",
    stopped: "Stopped · not verified",
  },
  zh: {
    checkingSources: "正在核对依据",
    stopped: "已停止 · 未核对依据",
  },
} as const;

type DraftLocale = keyof typeof DRAFT_COPY;

/** A label like `"S3"`/`"R1"` (no brackets — matches `RetrievalHitsLabelView.label`),
 * matched against the `[S3]`/`[R1]` markers `reduceStreamEvent` may still be
 * assembling mid-word in an in-flight draft. */
const LABEL_PATTERN = /\[(S\d+|R\d+)\]/g;

interface DraftMdNode {
  type: string;
  value?: string;
  children?: DraftMdNode[];
  data?: { hName?: string; hProperties?: Record<string, string> };
}

/** The mdast node spliced in for one recognized label: an empty
 * `<code data-claim-id="...">` element, the same hName/hProperties
 * convention `GroundedAnswer`'s citation badges use (and the same
 * `answerSchema` allowlists), repurposed here to carry a label string
 * instead of a claim id. */
function labelBadgeNode(label: string): DraftMdNode {
  return {
    type: "tapperDraftLabel",
    children: [],
    data: { hName: "code", hProperties: { dataClaimId: label } },
  };
}

/**
 * Splits one text node's value on every `[S3]`/`[R1]`-shaped marker whose
 * label is present in `citationLabels`, replacing just those with a badge
 * node; a marker whose label is not yet known (or never arrives) is left as
 * plain text, unmatched and unmodified, per the Task 8 contract.
 */
function splitLabelText(
  value: string,
  citationLabels: Readonly<Record<string, CitationLabelInfo>>,
): DraftMdNode[] {
  const result: DraftMdNode[] = [];
  let lastIndex = 0;
  for (const match of value.matchAll(LABEL_PATTERN)) {
    const label = match[1]!;
    if (!Object.hasOwn(citationLabels, label)) continue;
    const start = match.index!;
    const end = start + match[0].length;
    if (start > lastIndex) {
      result.push({ type: "text", value: value.slice(lastIndex, start) });
    }
    result.push(labelBadgeNode(label));
    lastIndex = end;
  }
  if (lastIndex < value.length || result.length === 0) {
    result.push({ type: "text", value: value.slice(lastIndex) });
  }
  return result;
}

function visitDraftLabels(
  node: DraftMdNode,
  citationLabels: Readonly<Record<string, CitationLabelInfo>>,
): void {
  const children = node.children;
  if (children === undefined) return;
  const next: DraftMdNode[] = [];
  for (const child of children) {
    if (child.type === "text" && typeof child.value === "string") {
      next.push(...splitLabelText(child.value, citationLabels));
    } else {
      visitDraftLabels(child, citationLabels);
      next.push(child);
    }
  }
  node.children = next;
}

/** A unified/remark plugin (factory) that splices a badge node in place of
 * every recognized `[S3]`/`[R1]` marker in the draft's own Markdown AST —
 * mirrors `citationRemarkPlugin` in `GroundedAnswer.tsx`, but matches by
 * marker text directly instead of by claim offset, since a draft has no
 * claim spans yet. */
function draftLabelRemarkPlugin({
  citationLabels,
}: {
  citationLabels: Readonly<Record<string, CitationLabelInfo>>;
}) {
  return (tree: DraftMdNode): void => {
    visitDraftLabels(tree, citationLabels);
  };
}

const DraftLabelContext = createContext<
  Readonly<Record<string, CitationLabelInfo>>
>({});

function DraftLabelBadge({
  children,
  className,
  "data-claim-id": label,
}: {
  children?: ReactNode;
  className?: string;
  "data-claim-id"?: string;
}) {
  const citationLabels = useContext(DraftLabelContext);
  const info = typeof label === "string" ? citationLabels[label] : undefined;
  if (info === undefined || label === undefined) {
    return <code className={className}>{children}</code>;
  }
  const title =
    info.location === null ? info.title : `${info.title} · ${info.location}`;
  return (
    <span className="tapper-draft-citation" title={title}>
      {`[${label}]`}
    </span>
  );
}

const DRAFT_MARKDOWN_COMPONENTS = {
  a: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  code: DraftLabelBadge,
};

function DraftMarkdown({
  text,
  citationLabels,
}: {
  text: string;
  citationLabels: Readonly<Record<string, CitationLabelInfo>>;
}) {
  const remarkOptions = useMemo(() => ({ citationLabels }), [citationLabels]);
  if (text.length === 0) return null;
  return (
    <div className="tapper-markdown tapper-draft-markdown">
      <DraftLabelContext.Provider value={citationLabels}>
        <ReactMarkdown
          remarkPlugins={[[draftLabelRemarkPlugin, remarkOptions]]}
          rehypePlugins={[[rehypeSanitize, answerSchema]]}
          components={DRAFT_MARKDOWN_COMPONENTS}
        >
          {text}
        </ReactMarkdown>
      </DraftLabelContext.Provider>
    </div>
  );
}

/**
 * The "generating" view over a Turn's current attempt: a thin wrapper
 * around the same safe-Markdown rendering and badge presentation
 * `GroundedAnswer` uses for the terminal answer (Task 3), shown instead of
 * it while no complete `RetrievalAnswerResponse` exists yet.
 *
 * - Not canceled: renders `draft.text`, with a "Checking sources" notice
 *   once `stage === "verify"` (committed before verification runs).
 * - Canceled: renders `partialAnswer` (the already-redacted draft text at
 *   cancellation, if any) under a "Stopped · not verified" notice; with no
 *   partial draft, only the stopped notice is shown. A canceled turn's
 *   partial text never joins the citation system — labels inside it still
 *   render as badges from `citationLabels` (found before cancellation), but
 *   this text itself is never a `RetrievalAnswerResponse`.
 */
export function DraftAnswer({
  draft,
  citationLabels,
  stage,
  partialAnswer,
  canceled,
  locale = "zh",
}: {
  draft: DraftState | null;
  citationLabels: Readonly<Record<string, CitationLabelInfo>>;
  stage: "verify" | null;
  partialAnswer: string | null;
  canceled: boolean;
  locale?: DraftLocale;
}) {
  const copy = DRAFT_COPY[locale];
  if (canceled) {
    return (
      <div className="tapper-draft-answer tapper-draft-canceled">
        {partialAnswer !== null && partialAnswer.length > 0 ? (
          <DraftMarkdown text={partialAnswer} citationLabels={citationLabels} />
        ) : null}
        <p className="tapper-draft-status" role="status">
          {copy.stopped}
        </p>
      </div>
    );
  }
  return (
    <div className="tapper-draft-answer" aria-live="polite">
      <DraftMarkdown text={draft?.text ?? ""} citationLabels={citationLabels} />
      {stage === "verify" ? (
        <p className="tapper-draft-status" role="status">
          {copy.checkingSources}
        </p>
      ) : null}
    </div>
  );
}
