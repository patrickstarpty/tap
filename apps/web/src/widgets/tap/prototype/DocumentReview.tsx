import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "antd";
import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { AssistantTurn, LibrarySource, Locale } from "./model";
import "./DocumentReview.css";

type DocumentState = "processing" | "failed" | "review" | "published";
type Document = {
  id: string;
  name: string;
  version: string;
  state: DocumentState;
  checks: boolean[];
};
const initial: Document[] = [
  {
    id: "underwriting-v12",
    name: "Life underwriting guide · v1.2.md",
    version: "v1.2",
    state: "review",
    checks: [false, false, false],
  },
  {
    id: "underwriting-scan",
    name: "Underwriting rules — scanned.pdf",
    version: "v1.0",
    state: "failed",
    checks: [false, false, false],
  },
];
export function useDocumentReview(locale: Locale) {
  const [documents, setDocuments] = useState<Document[]>(() => {
    try {
      const saved: unknown = JSON.parse(
        localStorage.getItem("tap.prototype.document-reviews.v1") ?? "null",
      );
      if (
        Array.isArray(saved) &&
        saved.every(
          (d) =>
            d &&
            typeof d.id === "string" &&
            typeof d.name === "string" &&
            typeof d.version === "string" &&
            ["processing", "failed", "review", "published"].includes(d.state) &&
            Array.isArray(d.checks) &&
            d.checks.length === 3 &&
            d.checks.every((value: unknown) => typeof value === "boolean"),
        )
      )
        return saved;
    } catch {
      /* A damaged browser snapshot must not prevent opening Library. */
    }
    return initial;
  });
  useEffect(() => {
    try {
      localStorage.setItem(
        "tap.prototype.document-reviews.v1",
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
        status:
          d.state === "published"
            ? "ready"
            : d.state === "failed"
              ? "failed"
              : "processing",
        reviewState: d.state,
        description:
          d.state === "published"
            ? t("Published", "已发布")
            : d.state === "review"
              ? t("Ready for review", "待核对")
              : d.state === "failed"
                ? t("Text extraction failed", "文本提取失败")
                : t("Processing document", "正在处理资料"),
      })),
    [documents, locale],
  );
  const selected = documents.find((d) => d.id === inspected);
  function update(patch: Partial<Document>) {
    setDocuments((current) =>
      current.map((d) => (d.id === inspected ? { ...d, ...patch } : d)),
    );
  }
  function inspect(id: string, trigger?: HTMLElement) {
    opener.current = trigger ?? null;
    setInspected(id);
  }
  function upload(name: string) {
    const id = crypto.randomUUID();
    setDocuments((current) => [
      ...current,
      {
        id,
        name,
        version: "v1.0",
        state: "processing",
        checks: [false, false, false],
      },
    ]);
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
      ariaLabel={t("Document review", "资料核对")}
      className="tap-document-review"
      onClose={review.close}
      opener={review.opener.current}
    >
      <header>
        <div>
          <h2>{d.name}</h2>
          <p>
            {d.version} · {t("Knowledge library", "知识库")}
          </p>
        </div>
        <Button aria-label={t("Close", "关闭")} onClick={review.close}>
          {t("Close", "关闭")}
        </Button>
      </header>
      {d.state === "processing" ? (
        <div className="tap-document-state" role="status">
          <h3>{t("Processing document…", "正在处理资料…")}</h3>
          <p>
            {t(
              "Extracting text and locating source passages.",
              "正在提取文本并定位原文段落。",
            )}
          </p>
        </div>
      ) : d.state === "failed" ? (
        <div className="tap-document-state">
          <h3>{t("No readable text found", "未找到可提取的文本")}</h3>
          <p>
            {t(
              "Replace this scan with a text-based PDF, DOCX, MD or TXT file.",
              "请将扫描件替换为可提取文本的 PDF、DOCX、MD 或 TXT。",
            )}
          </p>
          <div className="tap-document-recovery">
            <Button onClick={() => review.update({ state: "processing" })}>
              {t("Retry processing", "重新处理")}
            </Button>
            <label>
              {t("Replace file", "替换文件")}
              <input
                type="file"
                accept=".pdf,.docx,.md,.txt"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file)
                    review.update({
                      name: file.name,
                      state: "processing",
                      checks: [false, false, false],
                    });
                }}
              />
            </label>
          </div>
        </div>
      ) : (
        <>
          <div className="tap-document-columns">
            <article className="tap-document-original">
              <h3>{t("Original document", "原文")}</h3>
              <h4>{t("4. Health disclosure", "4. 健康告知")}</h4>
              <p>
                {t(
                  "An application must include a completed health disclosure before submission.",
                  "投保申请提交前必须完成健康告知。",
                )}
              </p>
              <mark>
                {t(
                  "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
                  "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
                )}
              </mark>
              <p>
                {t(
                  "Keep entered information and allow the applicant to complete missing fields before resubmitting.",
                  "保留已填写信息，允许申请人补齐缺失项后再次提交。",
                )}
              </p>
            </article>
            <section>
              <h3>{t("Review extracted content", "核对提取内容")}</h3>
              <p>
                {t(
                  "Missing disclosure → block submission → HTTP 422",
                  "缺少健康告知 → 阻止提交 → HTTP 422",
                )}
              </p>
              <small>
                {t(
                  "Source: section 4, paragraphs 1–3",
                  "原文位置：第 4 节，段落 1–3",
                )}
              </small>
              <div className="tap-document-checks">
                {[
                  [
                    "Text and key values match the original",
                    "文本与关键数值和原文一致",
                  ],
                  ["Source locations are correct", "原文位置准确"],
                  ["Version and scope are correct", "版本与适用范围正确"],
                ].map(([en, zh], i) => (
                  <label key={en}>
                    <input
                      type="checkbox"
                      checked={d.checks[i]}
                      disabled={d.state === "published"}
                      onChange={(event) =>
                        review.update({
                          checks: d.checks.map((checked, index) =>
                            index === i ? event.target.checked : checked,
                          ),
                        })
                      }
                    />
                    {t(en!, zh!)}
                  </label>
                ))}
              </div>
            </section>
          </div>
          <footer>
            {d.state === "published" ? (
              <>
                <span role="status">
                  {t("Published to knowledge library", "已发布到知识库")}
                </span>
                <Button
                  type="primary"
                  onClick={() => {
                    onUse(d.id);
                    review.close();
                  }}
                >
                  {t("Ask Tapper", "向 Tapper 提问")}
                </Button>
              </>
            ) : (
              <>
                <span>
                  {t(
                    `${d.checks.filter(Boolean).length} of 3 checks completed`,
                    `已核对 ${d.checks.filter(Boolean).length} / 3 项`,
                  )}
                </span>
                <Button
                  type="primary"
                  disabled={!d.checks.every(Boolean)}
                  onClick={() => review.update({ state: "published" })}
                >
                  {t("Publish", "发布")}
                </Button>
              </>
            )}
          </footer>
        </>
      )}
    </AccessibleDialog>
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
        <p>{t("Searching published sources…", "正在检索已发布资料…")}</p>
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
              <p>
                {t("Published version · Section 4", "已发布版本 · 第 4 节")}
              </p>
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
