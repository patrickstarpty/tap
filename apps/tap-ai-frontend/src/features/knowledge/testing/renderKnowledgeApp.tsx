import type { ReactElement, ReactNode } from "react";

import {
  createTestQueryClient,
  renderApp,
} from "../../../shared/testing/renderApp";
import { KnowledgeClientProvider } from "../api/queries";
import type { KnowledgeClient } from "../api/types";

interface KnowledgeRenderOptions extends Omit<
  NonNullable<Parameters<typeof renderApp>[1]>,
  "provider"
> {
  api: KnowledgeClient;
  /**
   * Seed `["runtime-mode"]` synchronously (the default). Pass `false` to let
   * the runtime-mode query resolve through a `RuntimeClientProvider` instead,
   * so `projectId` arrives asynchronously as it does at runtime.
   */
  seedRuntimeMode?: boolean;
}

export function renderKnowledgeApp(
  ui: ReactElement,
  { api, seedRuntimeMode = true, ...options }: KnowledgeRenderOptions,
) {
  const queryClient = options.queryClient ?? createTestQueryClient();
  if (seedRuntimeMode) {
    queryClient.setQueryData(["runtime-mode"], {
      mode: "validation",
      identityMode: "validation",
      projectId: api.projectId,
      actorId: "actor-test",
    });
  }
  queryClient.setQueryData(["model-catalog", api.projectId], {
    defaultAlias: "qwen-plus",
    items: [
      {
        alias: "qwen-plus",
        displayName: "Qwen Plus",
        capabilities: ["chat", "structured"],
      },
      {
        alias: "qwen-max",
        displayName: "Qwen Max",
        capabilities: ["chat", "structured"],
      },
    ],
  });
  function Provider({ children }: { children: ReactNode }) {
    return (
      <KnowledgeClientProvider client={api}>{children}</KnowledgeClientProvider>
    );
  }

  return renderApp(ui, { ...options, queryClient, provider: Provider });
}
