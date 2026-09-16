import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
} from "react";
import { createRuntimeClient, type RuntimeClient } from "./client";

const runtimeClient = createRuntimeClient();
const RuntimeClientContext = createContext<RuntimeClient>(runtimeClient);
export const runtimeKeys = { mode: ["runtime-mode"] as const };

export function RuntimeClientProvider({
  children,
  client = runtimeClient,
}: {
  children: ReactNode;
  client?: RuntimeClient;
}) {
  return createElement(
    RuntimeClientContext.Provider,
    { value: client },
    children,
  );
}

export function useRuntimeModeQuery() {
  const client = useContext(RuntimeClientContext);
  return useQuery({
    queryKey: runtimeKeys.mode,
    queryFn: ({ signal }) => client.getMode(signal),
    staleTime: Infinity,
    retry: false,
  });
}
