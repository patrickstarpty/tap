import { Tabs } from "antd";
import { StrictMode, createElement } from "react";
import { createRoot } from "react-dom/client";

import { AppProviders } from "../../src/app/providers";
import "../../src/app/styles.css";
import { KnowledgeLibrary } from "../../src/features/knowledge/components/KnowledgeLibrary";
import { COPY } from "../../src/features/knowledge/copy";
import { TapperWorkspace } from "../../src/widgets/tapper/TapperWorkspace";

function TapperKnowledgeHarness() {
  return createElement(
    "div",
    { className: "tapper-shell min-h-dvh" },
    createElement(
      "header",
      { className: "tapper-header" },
      createElement(
        "div",
        {
          className:
            "mx-auto flex w-full max-w-[1480px] items-center justify-between px-5 py-4 sm:px-8",
        },
        createElement("div", { className: "tapper-wordmark" }, COPY.appName),
        createElement(
          "div",
          { className: "tapper-workspace-name" },
          COPY.workspaceName,
        ),
      ),
    ),
    createElement(
      "main",
      { className: "mx-auto w-full max-w-[1480px] px-4 pb-12 sm:px-8" },
      createElement(Tabs, {
        className: "tapper-primary-tabs",
        defaultActiveKey: "ask",
        destroyOnHidden: false,
        items: [
          {
            key: "ask",
            label: COPY.askTab,
            children: createElement(TapperWorkspace),
          },
          {
            key: "library",
            label: COPY.libraryTab,
            children: createElement(KnowledgeLibrary),
          },
        ],
      }),
    ),
  );
}

const root = document.getElementById("root");

if (root === null) {
  throw new Error("Tapper E2E harness root element is missing.");
}

createRoot(root).render(
  createElement(
    StrictMode,
    null,
    createElement(AppProviders, null, createElement(TapperKnowledgeHarness)),
  ),
);
