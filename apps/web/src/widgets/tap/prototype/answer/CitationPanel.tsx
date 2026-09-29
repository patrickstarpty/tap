import { useState } from "react";
import { Button } from "antd";
import type { AssistantSourceReference, Locale } from "../model";

export interface OpenCitation {
  turnId: string;
  index: number;
  source: AssistantSourceReference & {
    hasNewerRevision?: boolean;
    description?: string;
  };
  verificationFailed?: boolean;
  sourceRemoved?: boolean;
}

const UNDERWRITING_SOURCE_NAME = /underwriting|健康|核保/i;

const BENEFICIARY_WORKFLOW_PASSAGE = {
  en: "Beneficiary shares must total 100%. Once submitted, the change takes effect immediately.",
  zh: "受益人份额之和必须为 100%。提交后变更立即生效。",
};

const BENEFICIARY_TEST_CASES_PASSAGE = {
  en: "A beneficiary change only takes effect after the insurer confirms the request.",
  zh: "受益人变更须经保险公司确认后才生效。",
};

const HEALTH_DISCLOSURE_PASSAGE = {
  en: "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
  zh: "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
};

function resolvePassage(
  source: OpenCitation["source"],
  t: (en: string, zh: string) => string,
): { heading: string; passage: string } {
  if (source.id === "sample-beneficiary") {
    return {
      heading: t("Beneficiary change", "受益人变更"),
      passage: t(
        BENEFICIARY_WORKFLOW_PASSAGE.en,
        BENEFICIARY_WORKFLOW_PASSAGE.zh,
      ),
    };
  }
  if (source.id === "sample-test-cases") {
    return {
      heading: t("Beneficiary change", "受益人变更"),
      passage: t(
        BENEFICIARY_TEST_CASES_PASSAGE.en,
        BENEFICIARY_TEST_CASES_PASSAGE.zh,
      ),
    };
  }
  if (UNDERWRITING_SOURCE_NAME.test(source.name)) {
    return {
      heading: t("Health disclosure", "健康告知"),
      passage: t(HEALTH_DISCLOSURE_PASSAGE.en, HEALTH_DISCLOSURE_PASSAGE.zh),
    };
  }
  return {
    heading: t("Excerpt", "摘录"),
    passage:
      source.description ??
      t(
        "This document has no highlighted passage for this citation.",
        "该文档没有此引用对应的高亮段落。",
      ),
  };
}

export function CitationPanel({
  citation,
  locale,
  onClose,
  onOpenOriginal,
}: {
  citation: OpenCitation;
  locale: Locale;
  onClose: () => void;
  onOpenOriginal: (sourceId: string) => void;
}) {
  const [verificationFailed, setVerificationFailed] = useState(
    citation.verificationFailed ?? false,
  );
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  const version = citation.source.hasNewerRevision ? "v1.0" : "v1.2";
  const { heading, passage } = resolvePassage(citation.source, t);
  return (
    <aside
      id="tap-knowledge-sources"
      className="tap-sources tap-citation-panel"
      aria-labelledby="tap-citation-heading"
    >
      <header>
        <div>
          <h2 id="tap-citation-heading">
            {t(`Citation [${citation.index}]`, `引用 [${citation.index}]`)}
          </h2>
          <p>{citation.source.name}</p>
          <p>
            {t(
              `Version ${version} · Section 4`,
              `版本 ${version} · 第 4 节`,
            )}
          </p>
        </div>
      </header>
      {citation.source.hasNewerRevision ? (
        <div className="tap-citation-notice" data-tone="warning" role="alert">
          <p>
            {t(
              "This source has been updated. The cited passage may have changed.",
              "来源已更新，引用内容可能已变化。",
            )}
          </p>
          <Button onClick={() => onOpenOriginal(citation.source.id)}>
            {t("View latest version", "查看最新版本")}
          </Button>
        </div>
      ) : null}
      {verificationFailed ? (
        <div className="tap-citation-notice" data-tone="error" role="alert">
          <p>{t("The citation could not be verified.", "引用核验失败。")}</p>
          <Button onClick={() => setVerificationFailed(false)}>
            {t("Retry verification", "重试核验")}
          </Button>
        </div>
      ) : null}
      <article className="tap-document-original">
        <h3>{heading}</h3>
        <mark>{passage}</mark>
      </article>
      {citation.sourceRemoved ? (
        <p className="tap-citation-removed">
          {t(
            "This source is no longer available.",
            "该来源已不可用。",
          )}
        </p>
      ) : null}
      <div className="tap-citation-panel-actions">
        {citation.sourceRemoved ? null : (
          <Button onClick={() => onOpenOriginal(citation.source.id)}>
            {t("Open original", "打开原件")}
          </Button>
        )}
        <Button onClick={onClose}>{t("Back to sources", "返回来源")}</Button>
      </div>
    </aside>
  );
}
