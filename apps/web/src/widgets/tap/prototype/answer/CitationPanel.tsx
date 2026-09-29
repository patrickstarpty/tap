import { useState } from "react";
import { Button } from "antd";
import type { AssistantSourceReference, Locale } from "../model";

export interface OpenCitation {
  turnId: string;
  index: number;
  source: AssistantSourceReference & { hasNewerRevision?: boolean };
  verificationFailed?: boolean;
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
          <p>
            {t(
              `Version ${version} · Section 4`,
              `版本 ${version} · 第 4 节`,
            )}
          </p>
        </div>
      </header>
      {citation.source.hasNewerRevision ? (
        <div role="alert">
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
        <div role="alert">
          <p>{t("The citation could not be verified.", "引用核验失败。")}</p>
          <Button onClick={() => setVerificationFailed(false)}>
            {t("Retry verification", "重试核验")}
          </Button>
        </div>
      ) : null}
      <article className="tap-document-original">
        <h3>{t("Health disclosure", "健康告知")}</h3>
        <mark>
          {t(
            "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
            "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
          )}
        </mark>
      </article>
      <div className="tap-citation-panel-actions">
        <Button onClick={() => onOpenOriginal(citation.source.id)}>
          {t("Open original", "打开原件")}
        </Button>
        <Button onClick={onClose}>{t("Back to sources", "返回来源")}</Button>
      </div>
    </aside>
  );
}
