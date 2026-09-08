import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useMemo, useState, type ReactNode } from "react";

import { createKnowledgeClient } from "../features/knowledge/api/client";
import { KnowledgeClientProvider } from "../features/knowledge/api/queries";
import type { KnowledgeClient } from "../features/knowledge/api/types";
import {
  RuntimeClientProvider,
  useRuntimeModeQuery,
} from "../features/runtime/api/queries";
import type { RuntimeClient } from "../features/runtime/api/client";
import { tapperTheme } from "./theme";

interface AppProvidersProps {
  children: ReactNode;
  knowledgeClient?: KnowledgeClient;
  queryClient?: QueryClient;
  runtimeClient?: RuntimeClient;
}

function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}

export function AppProviders({
  children,
  knowledgeClient,
  queryClient,
  runtimeClient,
}: AppProvidersProps) {
  const [clients] = useState(() => ({
    query: queryClient ?? createAppQueryClient(),
  }));

  return (
    <ConfigProvider locale={zhCN} theme={tapperTheme}>
      <QueryClientProvider client={clients.query}>
        <RuntimeClientProvider
          {...(runtimeClient === undefined ? {} : { client: runtimeClient })}
        >
          <RuntimeKnowledgeProvider
            {...(knowledgeClient === undefined ? {} : { knowledgeClient })}
          >
            {children}
          </RuntimeKnowledgeProvider>
        </RuntimeClientProvider>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

function RuntimeKnowledgeProvider({
  children,
  knowledgeClient,
}: {
  children: ReactNode;
  knowledgeClient?: KnowledgeClient;
}) {
  const runtime = useRuntimeModeQuery();
  const projectId = runtime.isSuccess ? runtime.data.projectId : null;
  const client = useMemo(() => {
    if (projectId === null) return null;
    if (knowledgeClient !== undefined) {
      if (knowledgeClient.projectId !== projectId)
        throw new Error("Knowledge client project mismatch.");
      return knowledgeClient;
    }
    return createKnowledgeClient({ projectId });
  }, [projectId, knowledgeClient]);
  return (
    <KnowledgeClientProvider client={client}>
      {children}
    </KnowledgeClientProvider>
  );
}
