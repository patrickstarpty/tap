import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "antd";
import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { AssistantTurn, LibrarySource, Locale } from "./model";
import "./DocumentReview.css";
import { ChunkManager } from "./ChunkManager";

type DocumentState =
  | "processing"
  | "failed"
  | "review"
  | "reviewing"
  | "approved"
  | "published"
  | "withdrawn";
type ReviewComment = {
  id: string;
  section: number;
  quote: string;
  text: string;
  resolved: boolean;
};
type Document = {
  available?: boolean;
  chunkSetup?: boolean;
  id: string;
  name: string;
  version: string;
  state: DocumentState;
  checks: boolean[];
  revision: number;
  comments?: ReviewComment[];
  history: { action: string; revision: number; actorId: string }[];
};
const initial: Document[] = [
  {
    id: "underwriting-v12",
    name: "Life underwriting guide · v1.2.md",
    version: "v1.2",
    state: "review",
    checks: [false, false, false, false],
    revision: 1,
    history: [],
  },
  {
    id: "underwriting-scan",
    name: "Underwriting rules — scanned.pdf",
    version: "v1.0",
    state: "failed",
    checks: [false, false, false, false],
    revision: 1,
    history: [],
  },
  {
    id: "underwriting-evidence-pdf",
    name: "Underwriting evidence.pdf",
    version: "v1.0",
    state: "review",
    checks: [false, false, false, false],
    revision: 1,
    history: [],
  },
  {
    id: "premium-rates-xlsx",
    name: "Premium rates.xlsx",
    version: "v1.0",
    state: "review",
    checks: [false, false, false, false],
    revision: 1,
    history: [],
  },
  {
    id: "approval-flow-complex",
    name: "Underwriting approval flow.png",
    version: "v1.0",
    state: "review",
    checks: [false, false, false, false],
    revision: 1,
    history: [],
  },
  {
    id: "health-disclosure-approved",
    name: "Health disclosure policy · approved.md",
    version: "v1.0",
    state: "approved",
    checks: [true, true, true, true],
    revision: 3,
    history: [
      { action: "submitted", revision: 2, actorId: "Content editor" },
      { action: "approved", revision: 3, actorId: "Independent reviewer" },
    ],
  },
];
export function useDocumentReview(locale: Locale) {
  const [documents, setDocuments] = useState<Document[]>(() => {
    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem("tap.prototype.document-reviews.v3") ?? "null",
      );
      if (
        Array.isArray(saved) &&
        saved.every(
          (d) =>
            d &&
            typeof d.id === "string" &&
            typeof d.name === "string" &&
            typeof d.version === "string" &&
            [
              "processing",
              "failed",
              "review",
              "reviewing",
              "approved",
              "published",
              "withdrawn",
            ].includes(d.state) &&
            Array.isArray(d.checks) &&
            d.checks.length === 4 &&
            d.checks.every((value: unknown) => typeof value === "boolean") &&
            Number.isInteger(d.revision) &&
            (d.comments === undefined ||
              (Array.isArray(d.comments) &&
                d.comments.every(
                  (c: ReviewComment) =>
                    c &&
                    typeof c.id === "string" &&
                    [3, 4, 5].includes(c.section) &&
                    typeof c.quote === "string" &&
                    typeof c.text === "string" &&
                    typeof c.resolved === "boolean",
                ))) &&
            Array.isArray(d.history) &&
            d.history.every(
              (event: unknown) =>
                typeof event === "object" &&
                event !== null &&
                "action" in event &&
                typeof event.action === "string" &&
                "revision" in event &&
                Number.isInteger(event.revision) &&
                "actorId" in event &&
                typeof event.actorId === "string",
            ),
        )
      )
        return [
          ...saved,
          ...initial.filter(
            (document) => !saved.some((item) => item.id === document.id),
          ),
        ];
    } catch {
      /* A damaged browser snapshot must not prevent opening Library. */
    }
    return initial;
  });
  useEffect(() => {
    try {
      localStorage.setItem(
        "tap.prototype.document-reviews.v3",
        JSON.stringify(documents),
      );
    } catch {
      /* Continue the current session if browser storage is unavailable. */
    }
  }, [documents]);
  const [inspected, setInspected] = useState<string | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  useEffect(() => {
    if (!documents.some((d) => d.state === "processing")) return;
    const timer = setTimeout(
      () =>
        setDocuments((current) =>
          current.map((d) =>
            d.state !== "processing"
              ? d
              : { ...d, state: /scan/i.test(d.name) ? "failed" : "review" },
          ),
        ),
      900,
    );
    return () => clearTimeout(timer);
  }, [documents]);
  const sources = useMemo<LibrarySource[]>(
    () =>
      documents.map((d) => ({
        id: d.id,
        name: d.name,
        origin: "knowledge-base",
        type: d.name.split(".").pop()!.toUpperCase(),
        preview:
          d.id === "approval-flow-complex"
            ? { imageUrl: "/prototype-files/underwriting-approval-flow.png" }
            : undefined,
        status:
          d.state === "failed"
            ? "failed"
            : d.state === "processing" || d.available === false
              ? "processing"
              : "ready",
        reviewState: d.state,
        description:
          d.state === "failed"
            ? t("Text extraction failed", "文本提取失败")
            : d.state === "processing"
              ? t("Processing document", "正在处理资料")
              : d.available === false
                ? t("No searchable chunks", "暂无可检索切片")
                : t("Ready for retrieval", "可检索"),
      })),
    [documents, locale],
  );
  const selected = documents.find((d) => d.id === inspected);
  function update(patch: Partial<Document>) {
    setDocuments((current) =>
      current.map((d) => {
        if (d.id !== inspected) return d;
        if (
          Object.entries(patch).every(
            ([key, value]) => d[key as keyof Document] === value,
          )
        )
          return d;
        const changedState =
          patch.state !== undefined && patch.state !== d.state;
        const changedReview = changedState || patch.comments !== undefined;
        const commentAction =
          patch.comments &&
          (patch.comments.length > (d.comments ?? []).length
            ? `Comment saved · ${patch.comments.at(-1)!.text}`
            : "Comment resolved");
        return {
          ...d,
          ...patch,
          revision: changedReview ? d.revision + 1 : d.revision,
          history: changedReview
            ? [
                ...d.history,
                {
                  action: commentAction || patch.state!,
                  revision: d.revision + 1,
                  actorId:
                    patch.state === "reviewing" || patch.comments
                      ? "Content editor"
                      : patch.state === "published" ||
                          patch.state === "withdrawn"
                        ? "Publisher"
                        : "Independent reviewer",
                },
              ]
            : d.history,
        };
      }),
    );
  }
  function inspect(id: string, trigger?: HTMLElement) {
    opener.current = trigger ?? null;
    setInspected(id);
  }
  function upload(name: string, options: { inspect?: boolean } = {}) {
    const id = crypto.randomUUID();
    setDocuments((current) => [
      ...current,
      {
        id,
        name,
        version: "v1.0",
        state: "review",
        chunkSetup: true,
        checks: [false, false, false, false],
        revision: 1,
        history: [],
      },
    ]);
    if (options.inspect ?? true) setInspected(id);
    return id;
  }
  return {
    sources,
    selected,
    inspect,
    update,
    upload,
    opener,
    close: () => setInspected(null),
    t,
  };
}
export function DocumentReview({
  review,
  onUse,
}: {
  review: ReturnType<typeof useDocumentReview>;
  onUse: (sourceId: string) => void;
}) {
  const d = review.selected;
  if (!d) return null;
  const { t } = review;
  return (
    <AccessibleDialog
      ariaLabel={t("Document chunks", "文档切片")}
      className="tap-document-review"
      onClose={review.close}
      opener={review.opener.current}
    >
      <header>
        <div>
          <h2>{d.name}</h2>
          <p>
            {d.version} · {t("Knowledge library", "知识库")} ·{" "}
            {t("Document version", "文档版本")} {d.revision}
          </p>
        </div>
        <Button aria-label={t("Close", "关闭")} onClick={review.close}>
          {t("Close", "关闭")}
        </Button>
      </header>
      {d.state === "processing" ? (
        <p role="status">{t("Processing document…", "正在处理资料…")}</p>
      ) : d.state === "failed" ? (
        <section>
          <h3>{t("No readable text found", "未找到可提取的文本")}</h3>
          <p>
            {t(
              "Replace this scan with a text-based PDF, DOCX, MD, TXT or XLSX file.",
              "请替换为可提取文本的 PDF、DOCX、MD、TXT 或 XLSX 文件。",
            )}
          </p>
          <Button onClick={() => review.update({ state: "processing" })}>
            {t("Retry processing", "重新处理")}
          </Button>
          <label>
            {t("Replace file", "替换文件")}
            <input
              type="file"
              accept=".pdf,.docx,.md,.txt,.xlsx"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file)
                  review.update({ name: file.name, state: "processing" });
              }}
            />
          </label>
        </section>
      ) : (
        <ChunkManager
          key={d.id}
          id={d.id}
          t={t}
          initialSettings={d.chunkSetup}
          onAvailability={(available) => review.update({ available })}
          originalView={
            <>
              <ReviewWorkbench document={d} review={review} />
              <details className="tap-document-history">
                <summary>
                  {t("Historical review records", "历史审核记录")}
                </summary>
                {d.history.length ? (
                  <ol>
                    {d.history.map((event) => (
                      <li key={event.revision}>
                        {event.action} · {event.actorId} · {event.revision}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>{t("No historical review records", "暂无历史审核记录")}</p>
                )}
              </details>
            </>
          }
        />
      )}
      <footer>
        <small>
          {t(
            "Save changes to update the index. Enabled chunks become searchable when ready.",
            "保存后更新索引，索引就绪后即可检索已启用切片。",
          )}
        </small>
        <Button
          type="primary"
          disabled={
            d.state === "failed" ||
            d.state === "processing" ||
            d.available === false
          }
          onClick={() => {
            onUse(d.id);
            review.close();
          }}
        >
          {t("Ask Tapper", "向 Tapper 提问")}
        </Button>
      </footer>
    </AccessibleDialog>
  );
}

type ReviewProps = {
  document: Document;
  review: ReturnType<typeof useDocumentReview>;
};
function ReviewWorkbench({ document: d, review }: ReviewProps) {
  const { t } = review;
  const textSections = [
    {
      number: 3,
      title: t("3. Application information", "3. 投保信息"),
      paragraphs: [
        t(
          "The application records the applicant’s information and supporting disclosures.",
          "投保申请记录申请人信息与相关告知内容。",
        ),
        t(
          "Check the applicable version and scope before using this guide.",
          "使用本指南前，请核对适用版本与范围。",
        ),
      ],
    },
    {
      number: 4,
      title: t("4. Health disclosure", "4. 健康告知"),
      paragraphs: [
        t(
          "An application must include a completed health disclosure before submission.",
          "投保申请提交前必须完成健康告知。",
        ),
        t(
          "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
          "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
        ),
        t(
          "Keep entered information and allow the applicant to complete missing fields before resubmitting.",
          "保留已填写信息，允许申请人补齐缺失项后再次提交。",
        ),
      ],
    },
    {
      number: 5,
      title: t("5. Submission and correction", "5. 提交与补充"),
      paragraphs: [
        t(
          "Review the application and completed disclosure together before resubmitting.",
          "再次提交前，一并核对投保申请与已补充的告知内容。",
        ),
        t(
          "Previously entered information remains available while the applicant completes missing fields.",
          "申请人补齐缺失项时，先前填写的信息应予保留。",
        ),
      ],
    },
  ];
  const pdfSections = [
    {
      number: 3,
      title: t("Page 3", "第 3 页"),
      paragraphs: [
        t(
          "The application must record the product and applicant identity.",
          "申请资料须记录产品与投保人身份。",
        ),
      ],
    },
    {
      number: 4,
      title: t("Page 4", "第 4 页"),
      paragraphs: [
        t(
          "Health disclosure is required before an underwriting decision.",
          "核保决定前须完成健康告知。",
        ),
        t(
          "The reviewer checks this passage against the original PDF page.",
          "复核人须对照 PDF 原件对应页核对本段。",
        ),
      ],
    },
    {
      number: 5,
      title: t("Page 5", "第 5 页"),
      paragraphs: [
        t(
          "Exceptions require a documented decision and approval.",
          "例外处理须记录决定和批准依据。",
        ),
      ],
    },
  ];
  const excelSections = [
    {
      number: 3,
      title: "Rates!A3:C3",
      paragraphs: [
        t("Product\tRate\tUnit", "产品\t费率\t单位"),
        t("Life cover\t0.25\t%", "寿险保障\t0.25\t%"),
      ],
    },
    {
      number: 4,
      title: "Rates!A4:C4",
      paragraphs: [
        t("Product\tRate\tUnit", "产品\t费率\t单位"),
        t("Critical illness\t0.35\t%", "重疾保障\t0.35\t%"),
      ],
    },
    {
      number: 5,
      title: "Rates!A5:C5",
      paragraphs: [
        t("Product\tRate\tUnit", "产品\t费率\t单位"),
        t("Accident\t0.18\t%", "意外保障\t0.18\t%"),
      ],
    },
  ];
  const flowchartSections = [
    {
      number: 3,
      title: t("3. Intake and completion", "3. 受理与补件"),
      paragraphs: [
        t(
          "Missing disclosure returns to applicant for correction.",
          "健康告知缺失时退回申请人补齐。",
        ),
        t(
          "Resubmission restarts the completeness check.",
          "补件后重新检查资料完整性。",
        ),
      ],
    },
    {
      number: 4,
      title: t("4. Risk and approval", "4. 风险与批准"),
      paragraphs: [
        t(
          "High risk requires senior review and compliance approval.",
          "高风险须经过资深核保与合规复核。",
        ),
        t(
          "Low risk proceeds through automatic approval.",
          "低风险进入自动核保批准。",
        ),
      ],
    },
    {
      number: 5,
      title: t("5. Payment and issuance", "5. 缴费与出单"),
      paragraphs: [
        t(
          "Failed payment returns to the applicant for retry.",
          "缴费失败时通知申请人重试。",
        ),
        t(
          "Successful payment leads to policy issuance.",
          "缴费成功后出具保单。",
        ),
      ],
    },
  ];
  const isFlowchart = d.id === "approval-flow-complex";
  const sections = isFlowchart
    ? flowchartSections
    : d.name.toLowerCase().endsWith(".xlsx")
      ? excelSections
      : d.name.toLowerCase().endsWith(".pdf")
        ? pdfSections
        : textSections;
  const [active, setActive] = useState(4);
  const [query, setQuery] = useState("");
  const [problems, setProblems] = useState(false);
  const [selection, setSelection] = useState("");
  const [quote, setQuote] = useState("");
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const drafts = useRef<Record<number, { quote: string; text: string }>>({});
  const sourceRef = useRef<HTMLElement>(null);
  const comments = d.comments ?? [];
  const openComments = comments.filter((comment) => !comment.resolved);
  const current = sections.find((section) => section.number === active)!;
  const filtered = sections.filter((section) =>
    `${section.title} ${section.number}`
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  const editable = d.state === "review";
  function markedParagraph(paragraph: string) {
    const quotes = comments
      .filter(
        (comment) =>
          comment.section === active &&
          comment.quote &&
          paragraph.includes(comment.quote),
      )
      .map((comment) => comment.quote);
    if (!quotes.length) return paragraph;
    const boundaries = new Set([0, paragraph.length]);
    const ranges = quotes.flatMap((value) => {
      const found: [number, number][] = [];
      let start = paragraph.indexOf(value);
      while (start !== -1) {
        found.push([start, start + value.length]);
        boundaries.add(start);
        boundaries.add(start + value.length);
        start = paragraph.indexOf(value, start + value.length);
      }
      return found;
    });
    const points = [...boundaries].sort((a, b) => a - b);
    return points.slice(0, -1).map((start, index) => {
      const end = points[index + 1]!;
      const content = paragraph.slice(start, end);
      return ranges.some(([left, right]) => start >= left && end <= right) ? (
        <mark data-review-mark key={start}>
          {content}
        </mark>
      ) : (
        <span key={start}>{content}</span>
      );
    });
  }
  function navigate(number: number) {
    if (!sections.some((section) => section.number === number)) return;
    drafts.current[active] = { quote, text };
    setActive(number);
    setSelection("");
    setQuote(drafts.current[number]?.quote ?? "");
    setText(drafts.current[number]?.text ?? "");
    setNotice("");
    window.getSelection()?.removeAllRanges();
  }
  function captureSelection() {
    const selected = window.getSelection();
    if (
      selected &&
      sourceRef.current?.contains(selected.anchorNode) &&
      sourceRef.current?.contains(selected.focusNode)
    )
      setSelection(selected.toString().trim());
  }
  return (
    <div className="tap-review-workbench">
      <aside className="tap-review-outline">
        <div className="tap-review-modes">
          <button
            type="button"
            aria-pressed={!problems}
            onClick={() => setProblems(false)}
          >
            {t("Outline", "目录")}
          </button>
          <button
            type="button"
            aria-pressed={problems}
            onClick={() => setProblems(true)}
          >
            {t(
              `Open comments (${openComments.length})`,
              `待核对 (${openComments.length})`,
            )}
          </button>
        </div>
        {problems ? (
          <div className="tap-review-problems">
            {openComments.length === 0 ? (
              <p>
                {t(
                  "No open comments. Select a passage to raise a question.",
                  "暂无待核对意见。选取原文可添加问题。",
                )}
              </p>
            ) : (
              openComments.map((comment) => (
                <button
                  key={comment.id}
                  type="button"
                  onClick={() => navigate(comment.section)}
                >
                  <small>
                    {
                      sections.find(
                        (section) => section.number === comment.section,
                      )?.title
                    }
                  </small>
                  {comment.text}
                </button>
              ))
            )}
          </div>
        ) : (
          <>
            <input
              type="search"
              aria-label={t("Find a section", "查找章节")}
              placeholder={t("Find a section…", "查找章节…")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <small>
              {t(
                `${filtered.length} of ${sections.length} sections`,
                `${filtered.length} / ${sections.length} 个章节`,
              )}
            </small>
            <nav aria-label={t("Document outline", "文档目录")}>
              {filtered.map((section) => (
                <button
                  key={section.number}
                  type="button"
                  aria-current={
                    section.number === active ? "location" : undefined
                  }
                  onClick={() => navigate(section.number)}
                >
                  {section.title}
                  <small>
                    {d.name.toLowerCase().endsWith(".xlsx")
                      ? t("1 row", "1 行")
                      : d.name.toLowerCase().endsWith(".pdf")
                        ? t("1 page", "1 页")
                        : t(
                            `${section.paragraphs.length} paragraphs`,
                            `${section.paragraphs.length} 段`,
                          )}
                  </small>
                </button>
              ))}
              {filtered.length === 0 && (
                <p>
                  {t(
                    "No matching sections. Try a title or section number.",
                    "未找到章节，请尝试标题或章节号。",
                  )}
                </p>
              )}
            </nav>
          </>
        )}
      </aside>
      <div className="tap-review-reader">
        <div className="tap-review-reader-toolbar">
          <strong>
            {isFlowchart
              ? t("Original image and extracted flow", "原图与流程解析")
              : d.name.toLowerCase().endsWith(".xlsx")
                ? t("Extracted cells", "提取单元格")
                : t("Extracted text", "提取文本")}
          </strong>
          <span>{d.version}</span>
        </div>
        <article
          ref={sourceRef}
          className="tap-document-original"
          onMouseUp={captureSelection}
          onKeyUp={captureSelection}
          tabIndex={0}
          aria-label={t("Source passage", "来源段落")}
        >
          {isFlowchart ? (
            <div className="tap-review-flowchart-preview">
              <img
                src="/prototype-files/underwriting-approval-flow.png"
                alt={t(
                  "Original underwriting approval flowchart",
                  "核保审批流程图原图",
                )}
              />
              <a
                href="/prototype-files/underwriting-approval-flow.png"
                target="_blank"
                rel="noreferrer"
              >
                {t("Open full image", "查看完整原图")}
              </a>
            </div>
          ) : null}
          <h3>{current.title}</h3>
          {d.name.toLowerCase().endsWith(".xlsx") ? (
            <div className="tap-review-table-scroll">
              <table aria-label={t("Extracted worksheet row", "提取工作表行")}>
                <thead>
                  <tr>
                    {current.paragraphs[0]!.split("\t").map((cell, index) => (
                      <th key={index} scope="col">
                        {cell}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {current.paragraphs[1]!.split("\t").map((cell, index) => (
                      <td key={index}>{markedParagraph(cell)}</td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          ) : (
            current.paragraphs.map((paragraph, index) => (
              <p key={paragraph}>
                {active === 4 && index === 1 && !isFlowchart ? (
                  <mark>{markedParagraph(paragraph)}</mark>
                ) : (
                  markedParagraph(paragraph)
                )}
              </p>
            ))
          )}
        </article>
        <div className="tap-review-reader-actions">
          <Button
            disabled={!editable || !selection}
            onClick={() => {
              setQuote(selection);
              setNotice("");
            }}
          >
            {t("Mark selected text", "标记选中文字")}
          </Button>
          <small>
            {t(
              "Select text to attach a comment. The source stays unchanged.",
              "选取文字添加意见，原文保持不变。",
            )}
          </small>
        </div>
        <div className="tap-review-pagination">
          <Button disabled={active === 3} onClick={() => navigate(active - 1)}>
            {t("Previous section", "上一节")}
          </Button>
          <small>
            {active - 2} / {sections.length}
          </small>
          <Button disabled={active === 5} onClick={() => navigate(active + 1)}>
            {t("Next section", "下一节")}
          </Button>
        </div>
      </div>
      <section
        className="tap-review-comments"
        aria-label={t("Section comments", "章节意见")}
      >
        <h3>{t("Review comments", "核对意见")}</h3>
        <small>
          {d.name.toLowerCase().endsWith(".xlsx")
            ? t("Worksheet row", "工作表行")
            : d.name.toLowerCase().endsWith(".pdf")
              ? t("PDF page", "PDF 页")
              : t("Section", "章节")}{" "}
          · {current.title}
        </small>
        {comments
          .filter((comment) => comment.section === active)
          .map((comment) => (
            <div className="tap-review-comment" key={comment.id}>
              <blockquote>{comment.quote}</blockquote>
              <p>{comment.text}</p>
              {comment.resolved ? (
                <small>{t("Resolved", "已解决")}</small>
              ) : (
                <Button
                  disabled={!editable}
                  onClick={() =>
                    review.update({
                      comments: comments.map((item) =>
                        item.id === comment.id
                          ? { ...item, resolved: true }
                          : item,
                      ),
                    })
                  }
                >
                  {t("Resolve comment", "标为已解决")}
                </Button>
              )}
            </div>
          ))}
        {editable && (
          <div className="tap-review-comment-form">
            {quote ? (
              <blockquote>{quote}</blockquote>
            ) : (
              <p>
                {t(
                  "Mark a passage or leave a comment for this section.",
                  "标记一段文字，或为本节填写核对意见。",
                )}
              </p>
            )}
            <label htmlFor="tap-review-comment">
              {t("Review comment", "核对意见")}
            </label>
            <textarea
              id="tap-review-comment"
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={4}
              placeholder={t("What needs to be checked?", "哪些内容需要核对？")}
            />
            <Button
              disabled={!text.trim()}
              onClick={() => {
                review.update({
                  comments: [
                    ...comments,
                    {
                      id: crypto.randomUUID(),
                      section: active,
                      quote,
                      text: text.trim(),
                      resolved: false,
                    },
                  ],
                });
                setQuote("");
                setText("");
                setSelection("");
                setNotice(t("Comment saved", "意见已保存"));
              }}
            >
              {t("Save comment", "保存意见")}
            </Button>
            <small aria-live="polite">{notice}</small>
          </div>
        )}
        {openComments.length > 0 && (
          <p className="tap-review-open-notice">
            {t(
              "Comments are retained with the original document.",
              "核对意见保留在原始文档中。",
            )}
          </p>
        )}
      </section>
    </div>
  );
}
export function KnowledgeAnswer({
  turn,
  onRetry,
  onStop,
}: {
  turn: AssistantTurn;
  onRetry: () => void;
  onStop: () => void;
}) {
  const [citation, setCitation] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const t = (en: string, zh: string) => (turn.locale === "zh" ? zh : en);
  if (turn.answerState === "running")
    return (
      <div role="status">
        <p>{t("Searching enabled sources…", "正在检索已启用资料…")}</p>
        <Button onClick={onStop}>{t("Stop", "停止生成")}</Button>
      </div>
    );
  if (turn.answerState === "canceled" || turn.answerState === "failed")
    return (
      <div>
        <p>
          {turn.answerState === "canceled"
            ? t("Generation stopped.", "已停止生成。")
            : t(
                "The answer could not be generated. Please try again.",
                "回答生成失败，请重试。",
              )}
        </p>
        <Button onClick={onRetry}>{t("Retry", "重试")}</Button>
      </div>
    );
  if (turn.answerState === "insufficient")
    return (
      <div>
        <p>
          {t(
            "The available sources do not contain enough evidence to answer this question.",
            "现有资料不足以支持这个问题的结论。",
          )}
        </p>
        <p>
          {t(
            "Choose a relevant source, narrow your question, or add supporting documents to Library.",
            "请选择相关来源、缩小问题范围，或在知识库补充资料。",
          )}
        </p>
      </div>
    );
  return (
    <div className="tap-knowledge-answer">
      <p>
        {t(
          "Block submission when health disclosure is missing. Return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED, retain entered information, and prompt the applicant to complete the disclosure before resubmitting.",
          "缺少健康告知时，应阻止提交并返回 HTTP 422 与 HEALTH_DISCLOSURE_REQUIRED。保留已填信息，提示申请人补充后再提交。",
        )}
      </p>
      <Button
        type="link"
        onClick={(event) => {
          opener.current = event.currentTarget;
          setCitation(true);
        }}
      >
        [1] {t("Health disclosure · Section 4", "健康告知 · 第 4 节")}
      </Button>
      {citation ? (
        <AccessibleDialog
          ariaLabel={t("Source citation", "原文引用")}
          className="tap-document-review tap-document-citation"
          opener={opener.current}
          onClose={() => setCitation(false)}
        >
          <header>
            <div>
              <h2>{turn.sourceReferences[0]?.name}</h2>
              <p>{t("Source version · Section 4", "来源版本 · 第 4 节")}</p>
            </div>
            <Button
              onClick={() => setCitation(false)}
              aria-label={t("Close citation", "关闭引用")}
            >
              {t("Close", "关闭")}
            </Button>
          </header>
          <article className="tap-document-original">
            <h3>{t("Health disclosure", "健康告知")}</h3>
            <mark>
              {t(
                "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
                "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
              )}
            </mark>
          </article>
        </AccessibleDialog>
      ) : null}
    </div>
  );
}
