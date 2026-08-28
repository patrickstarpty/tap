import { Tabs, Typography } from "antd";

import { KnowledgeLibrary } from "../features/knowledge/components/KnowledgeLibrary";
import { COPY } from "../features/knowledge/copy";

function AskShell() {
  return (
    <section className="athena-ask-shell" aria-labelledby="ask-shell-heading">
      <div className="athena-ask-rule" aria-hidden />
      <Typography.Text className="athena-eyebrow">
        {COPY.workspaceName}
      </Typography.Text>
      <Typography.Title level={2} id="ask-shell-heading">
        {COPY.askTitle}
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        {COPY.askDescription}
      </Typography.Paragraph>
    </section>
  );
}

export function AthenaPage({
  knowledgePollIntervalMs,
}: { knowledgePollIntervalMs?: number } = {}) {
  return (
    <div className="athena-shell min-h-dvh">
      <header className="athena-header">
        <div className="mx-auto flex w-full max-w-[1480px] items-center justify-between px-5 py-4 sm:px-8">
          <div className="athena-wordmark">{COPY.appName}</div>
          <div className="athena-workspace-name">{COPY.workspaceName}</div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1480px] px-4 pb-12 sm:px-8">
        <Tabs
          className="athena-primary-tabs"
          defaultActiveKey="ask"
          destroyOnHidden={false}
          items={[
            { key: "ask", label: COPY.askTab, children: <AskShell /> },
            {
              key: "library",
              label: COPY.libraryTab,
              children: (
                <KnowledgeLibrary pollIntervalMs={knowledgePollIntervalMs} />
              ),
            },
          ]}
        />
      </main>
    </div>
  );
}
