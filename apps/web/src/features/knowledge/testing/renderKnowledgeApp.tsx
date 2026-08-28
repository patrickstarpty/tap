import type { ReactElement, ReactNode } from "react";

import { renderApp } from "../../../shared/testing/renderApp";
import { KnowledgeClientProvider } from "../api/queries";
import type { KnowledgeClient } from "../api/types";

interface KnowledgeRenderOptions extends Omit<
  NonNullable<Parameters<typeof renderApp>[1]>,
  "provider"
> {
  api: KnowledgeClient;
}

export function renderKnowledgeApp(
  ui: ReactElement,
  { api, ...options }: KnowledgeRenderOptions,
) {
  function Provider({ children }: { children: ReactNode }) {
    return (
      <KnowledgeClientProvider client={api}>{children}</KnowledgeClientProvider>
    );
  }

  return renderApp(ui, { ...options, provider: Provider });
}
