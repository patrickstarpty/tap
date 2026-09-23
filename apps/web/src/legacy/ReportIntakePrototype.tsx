import { useRef, useState } from "react";
import type { Locale } from "./model";
import { AccessibleDialog } from "./AccessibleDialog";
import { type Build, type Execution } from "./testAnalyticsModel";
import "./ReportIntakePrototype.css";
export type ReportFormat = "allure" | "junit";
export type ReportScenario = "ready" | "missing" | "invalid";
export function displayExecutionLog(log: string | undefined): string {
  return (log ?? "")
    .split("\n")
    .filter((line) => !/^\s*\[(fixture|context|test)\]/.test(line))
    .join("\n")
    .trim();
}
export function ReportIntakePrototype({
  locale = "zh",
  format,
  scenario,
  buildId,
  receivedBuilds = [],
  builds,
  scopedBuilds,
  projectName,
  onBuild,
  onImport,
}: {
  locale?: Locale;
  format: ReportFormat;
  scenario: ReportScenario;
  buildId: string;
  receivedBuilds?: readonly string[];
  builds: readonly Build[];
  scopedBuilds: readonly Build[];
  projectName: string;
  onBuild: (value: string) => void;
  onImport: (format: ReportFormat, buildId: string) => void;
}) {
  const t = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(format);
  const [targetBuild, setTargetBuild] = useState(buildId || "BUILD-2867");
  const [qualityOpen, setQualityOpen] = useState(false);
  const incomplete = scopedBuilds.filter(
    (build) =>
      ["BUILD-2868", "BUILD-2869"].includes(build.id) &&
      !receivedBuilds.includes(build.id),
  );
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const imported = useRef(new Set<string>());
  const opener = useRef<HTMLElement | null>(null);
  const openIntake = (element: HTMLElement) => {
    opener.current = element;
    setDraft(format);
    setTargetBuild(buildId || "BUILD-2867");
    setFile(null);
    setError("");
    setOpen(true);
  };
  const importReport = () => {
    if (!file) {
      setError(t("请选择报告文件。", "Choose a report file."));
      return;
    }
    const extension = draft === "allure" ? ".zip" : ".xml";
    if (!file.name.toLowerCase().endsWith(extension)) {
      setError(
        draft === "allure"
          ? t(
              "请选择 Allure Results ZIP 文件。",
              "Choose an Allure Results ZIP file.",
            )
          : t("请选择 JUnit XML 文件。", "Choose a JUnit XML file."),
      );
      return;
    }
    if (file.size === 0) {
      setError(
        t(
          "报告文件为空，请重新选择。",
          "This report is empty. Choose another file.",
        ),
      );
      return;
    }
    const key = `${targetBuild}:${draft}:${file.name}:${file.size}:${file.lastModified}`;
    if (imported.current.has(key)) {
      setError(
        t(
          "此报告已接收，未重复计数。请选择其他报告。",
          "This report has already been received and was not counted again. Choose another report.",
        ),
      );
      return;
    }
    imported.current.add(key);
    onImport(draft, targetBuild);
    setNotice(t(`${file.name} 已接入`, `${file.name} imported`));
    setOpen(false);
  };
  return (
    <>
      <section className="bs-intake-strip">
        <label>
          {t("构建", "Build")}
          <select
            value={buildId}
            onChange={(event) => onBuild(event.target.value)}
          >
            <option value="">{t("全部构建", "All builds")}</option>
            {[...builds].reverse().map((build) => (
              <option key={build.id} value={build.id}>
                {build.id} · {build.environment === "qa" ? "QA" : "Staging"} ·{" "}
                {build.date.slice(5)}
              </option>
            ))}
          </select>
        </label>
        <div className="bs-data-coverage">
          <span>
            {t(
              `${scopedBuilds.length - incomplete.length} 次构建报告已接收`,
              `${scopedBuilds.length - incomplete.length} build reports received`,
            )}
          </span>
          {incomplete.length ? (
            <button
              className="bs-data-warning"
              aria-expanded={qualityOpen}
              onClick={() => setQualityOpen(!qualityOpen)}
            >
              {t(
                `${incomplete.length} 次构建需处理`,
                `${incomplete.length} ${incomplete.length === 1 ? "build needs" : "builds need"} attention`,
              )}
            </button>
          ) : null}
        </div>
        <button onClick={(event) => openIntake(event.currentTarget)}>
          {t("数据接入", "Data connections")}
        </button>
      </section>
      {qualityOpen && incomplete.length ? (
        <section
          className="bs-quality-details"
          aria-label={t("数据完整性", "Data completeness")}
        >
          <p>
            {t(
              "以下构建未纳入统计。处理后再查看相应数据。",
              "These builds are excluded from metrics until their reports are complete.",
            )}
          </p>
          {incomplete.map((build) => (
            <button
              key={build.id}
              onClick={() => {
                onBuild(build.id);
                setQualityOpen(false);
              }}
            >
              <strong>{build.id}</strong>
              <span>
                {build.id === "BUILD-2868"
                  ? t("缺少 1 份报告", "1 report missing")
                  : t("报告解析失败", "Report parsing failed")}
              </span>
              <span>{t("查看构建", "View build")}</span>
            </button>
          ))}
        </section>
      ) : null}
      {scenario !== "ready" ? (
        <section className="bs-data-empty" role="status">
          <h2>
            {scenario === "missing"
              ? t(
                  "报告缺失，暂不汇总",
                  "Reports missing \u00b7 metrics unavailable",
                )
              : t(
                  "报告无法解析，暂不汇总",
                  "Report parsing failed \u00b7 metrics unavailable",
                )}
          </h2>
          <p>
            {scenario === "missing"
              ? t(
                  "缺少 shard-04。请补传报告后查看此构建的指标。",
                  "shard-04 is missing. Upload its report to view metrics for this build.",
                )
              : t(
                  "未找到有效的测试结果。请在数据接入中替换报告。",
                  "No valid test results found. Replace the report in Data connections.",
                )}
          </p>
          <button onClick={(event) => openIntake(event.currentTarget)}>
            {scenario === "missing"
              ? t("补传报告", "Upload missing report")
              : t("替换报告", "Replace report")}
          </button>
        </section>
      ) : null}
      {notice ? (
        <p className="bs-intake-notice" role="status">
          {notice}
        </p>
      ) : null}
      {open ? (
        <AccessibleDialog
          ariaLabel={t("数据接入", "Data connections")}
          className="bs-report-dialog"
          onClose={() => setOpen(false)}
          opener={opener.current}
        >
          <header>
            <h2>{t("数据接入", "Data connections")}</h2>
            <button onClick={() => setOpen(false)}>{t("关闭", "Close")}</button>
          </header>
          <p>
            {t("项目", "Project")} · {projectName}
          </p>
          <div className="bs-report-columns">
            <section>
              <label className="bs-target-build">
                {t("所属构建", "Target build")}
                <select
                  value={targetBuild}
                  onChange={(event) => setTargetBuild(event.target.value)}
                >
                  {[...builds].reverse().map((build) => (
                    <option key={build.id} value={build.id}>
                      {build.id} ·{" "}
                      {build.environment === "qa" ? "QA" : "Staging"}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("报告格式", "Report format")}
                <select
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value as ReportFormat);
                    setFile(null);
                    setError("");
                  }}
                >
                  <option value="allure">Allure Results ZIP</option>
                  <option value="junit">JUnit XML</option>
                </select>
              </label>
              <label className="bs-report-upload">
                {t("报告文件", "Report file")}
                <input
                  key={draft}
                  type="file"
                  accept={draft === "allure" ? ".zip" : ".xml"}
                  onChange={(event) => {
                    setFile(event.target.files?.[0] ?? null);
                    setError("");
                  }}
                />
              </label>
              <small>
                {draft === "allure"
                  ? t(
                      "上传 allure-results 目录的 ZIP 压缩包。",
                      "Upload a ZIP archive of the allure-results directory.",
                    )
                  : t(
                      "上传测试工具生成的 JUnit XML 报告。",
                      "Upload a JUnit XML report generated by your test tools.",
                    )}
              </small>
              {file ? <p>{file.name}</p> : null}
              {error ? (
                <p className="bs-report-error" role="alert">
                  {error}
                </p>
              ) : null}
            </section>
            <aside>
              <h3>{t("接入说明", "Connection guide")}</h3>
              <details>
                <summary>
                  {t("使用 pytest 生成报告", "Generate reports with pytest")}
                </summary>
                <pre>
                  {draft === "allure"
                    ? "pytest --alluredir=allure-results"
                    : "pytest --junitxml=report.xml"}
                </pre>
              </details>
              <p>
                {draft === "allure"
                  ? t(
                      "请保留结果文件及其关联附件。",
                      "Keep the result files and their associated attachments together.",
                    )
                  : t(
                      "重试记录、步骤与附件以报告中提供的信息为准。",
                      "Retry history, steps and attachments depend on what the report contains.",
                    )}
              </p>
            </aside>
          </div>
          <footer>
            <button onClick={() => setOpen(false)}>
              {t("取消", "Cancel")}
            </button>
            <button className="bs-primary" onClick={importReport}>
              {t("确认导入", "Confirm import")}
            </button>
          </footer>
        </AccessibleDialog>
      ) : null}
    </>
  );
}
export function FailureKnowledgeExplanation({
  locale = "zh",
  execution,
  format,
}: {
  locale?: Locale;
  execution: Execution;
  format: ReportFormat;
}) {
  const t = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("logs");
  const validationMismatch = execution.attempts.some(
    (attempt) => attempt.errorId === "ERR-422",
  );
  return (
    <section className="bs-failure-explanation">
      <div
        className="bs-evidence-tabs"
        role="group"
        aria-label={t("执行资料", "Execution evidence")}
      >
        {[
          ["logs", t("报告信息", "Report information")],
          ["steps", t("步骤", "Steps")],
          ["attachments", t("附件", "Attachments")],
        ].map(([value, label]) => (
          <button
            aria-pressed={tab === value}
            key={value}
            onClick={() => setTab(value!)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "logs" ? (
        <p>
          {t("来源", "Source")}{" "}
          {format === "allure" ? "Allure Results" : "JUnit XML"} ·{" "}
          {execution.buildId} · {execution.testId}
        </p>
      ) : (
        <p>
          {format === "junit"
            ? t(
                "此 JUnit 报告未提供步骤或附件。",
                "This JUnit report does not include steps or attachments.",
              )
            : t(
                "当前报告未提供步骤或附件。",
                "This report does not include steps or attachments.",
              )}
        </p>
      )}
      <button className="bs-primary" onClick={() => setOpen(true)}>
        {t("AI 结合知识解释", "Explain with knowledge")}
      </button>
      {open ? (
        <article>
          <h3>
            {t("失败解释", "Failure explanation")}{" "}
            <small>
              {t("AI 分析 · 待验证", "AI analysis \u00b7 Unverified")}
            </small>
          </h3>
          <h4>{t("报告事实", "Report facts")}</h4>
          <p>
            {displayExecutionLog(
              execution.attempts.find((attempt) => attempt.status === "failed")
                ?.log,
            ) ||
              t("当前执行没有失败日志。", "This execution has no failure log.")}
          </p>
          {!validationMismatch ? (
            <>
              <h4>{t("知识依据不足", "Insufficient knowledge")}</h4>
              <p>
                {t(
                  "已发布知识中没有足以解释这条失败的规则。仅保留报告事实，不判断根因；请补充服务日志、环境状态或相应规范。",
                  "Published knowledge does not contain a rule that explains this failure. The root cause remains unconfirmed. Add service logs, environment details or the relevant specification.",
                )}
              </p>
            </>
          ) : (
            <>
              <h4>{t("与知识的关联", "Knowledge association")}</h4>
              <p>
                {t(
                  "已发布《健康告知校验规范》v1.1 第 4 节要求：缺少健康告知时返回 HTTP 422。报告中的 HTTP 200 与规范不一致。",
                  "Section 4 of the published Health Disclosure Validation Specification v1.1 requires HTTP 422 when health disclosure is missing. The reported HTTP 200 differs from that requirement.",
                )}
              </p>
              <details>
                <summary>
                  {t("[1] 查看原文引用", "[1] View source excerpt")}
                </summary>
                <blockquote>
                  {t(
                    "缺少健康告知时，系统应阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
                    "When health disclosure is missing, the system must block submission and return HTTP 422 with error code HEALTH_DISCLOSURE_REQUIRED.",
                  )}
                </blockquote>
                <small>
                  {t("已发布 · 第 4 节", "Published \u00b7 Section 4")}
                </small>
              </details>
              <h4>{t("建议核查", "Suggested checks")}</h4>
              <p>
                {t(
                  "核实本次请求是否确实缺少健康告知，再检查服务版本与校验分支。现有证据不足以断定是产品缺陷还是测试数据问题。",
                  "Confirm whether the request was missing health disclosure, then check the service version and validation logic. The evidence does not yet distinguish a product defect from a test data issue.",
                )}
              </p>
            </>
          )}
        </article>
      ) : null}
    </section>
  );
}
