import { Tabs } from "antd";

import { KnowledgeLibrary } from "../features/knowledge/components/KnowledgeLibrary";
import { COPY } from "../features/knowledge/copy";
import { AthenaWorkspace } from "../widgets/athena/AthenaWorkspace";

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
            {
              key: "ask",
              label: COPY.askTab,
              children: (
                <AthenaWorkspace pollIntervalMs={knowledgePollIntervalMs} />
              ),
            },
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
