import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useState, type ReactNode } from "react";

import { createKnowledgeClient } from "../features/knowledge/api/client";
import { KnowledgeClientProvider } from "../features/knowledge/api/queries";
import type { KnowledgeClient } from "../features/knowledge/api/types";
import { tapperTheme } from "./theme";

interface AppProvidersProps {
  children: ReactNode;
  knowledgeClient?: KnowledgeClient;
  queryClient?: QueryClient;
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
}: AppProvidersProps) {
  const [clients] = useState(() => ({
    knowledge: knowledgeClient ?? createKnowledgeClient(),
    query: queryClient ?? createAppQueryClient(),
  }));

  return (
    <ConfigProvider locale={zhCN} theme={tapperTheme}>
      <QueryClientProvider client={clients.query}>
        <KnowledgeClientProvider client={clients.knowledge}>
          {children}
        </KnowledgeClientProvider>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
