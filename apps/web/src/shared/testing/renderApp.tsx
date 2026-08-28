import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { ConfigProvider } from "antd";
import type { ComponentType, ReactElement, ReactNode } from "react";

const testQueryClients = new Set<QueryClient>();

interface AppRenderOptions extends Omit<RenderOptions, "wrapper"> {
  queryClient?: QueryClient;
  provider?: ComponentType<{ children: ReactNode }>;
}

export function createTestQueryClient(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  testQueryClients.add(queryClient);
  return queryClient;
}

export function clearTestQueryClients(): void {
  for (const queryClient of testQueryClients) queryClient.clear();
  testQueryClients.clear();
}

export function renderApp(
  ui: ReactElement,
  {
    queryClient = createTestQueryClient(),
    provider: Provider,
    ...options
  }: AppRenderOptions = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ConfigProvider theme={{ token: { motion: false } }}>
        <QueryClientProvider client={queryClient}>
          {Provider === undefined ? children : <Provider>{children}</Provider>}
        </QueryClientProvider>
      </ConfigProvider>
    );
  }

  return { ...render(ui, { wrapper: Wrapper, ...options }), queryClient };
}
