import type { components } from "../../../shared/api/generated/schema";
import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
} from "react";

export type ModelCatalogItem = components["schemas"]["ModelCatalogItem"];
export type ModelCatalogPage = components["schemas"]["ModelCatalogPage"];

export async function fetchModelCatalog(
  projectId: string,
  baseUrl = "",
): Promise<ModelCatalogPage> {
  const response = await fetch(
    `${baseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/ai/models`,
  );
  if (!response.ok) throw new Error("Model catalog request failed.");
  const value = (await response.json()) as ModelCatalogPage;
  if (
    !value ||
    typeof value.defaultAlias !== "string" ||
    !Array.isArray(value.items) ||
    Object.keys(value).some(
      (key) => !["defaultAlias", "items"].includes(key),
    ) ||
    value.items.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        typeof item.alias !== "string" ||
        typeof item.displayName !== "string" ||
        !Array.isArray(item.capabilities) ||
        item.capabilities.some(
          (kind) => !["chat", "embed", "structured"].includes(kind),
        ) ||
        Object.keys(item).some(
          (key) => !["alias", "displayName", "capabilities"].includes(key),
        ),
    ) ||
    !value.items.some(
      (item) =>
        item.alias === value.defaultAlias && item.capabilities.includes("chat"),
    )
  ) {
    throw new Error("Model catalog is unavailable.");
  }
  return value;
}

const CatalogLoader =
  createContext<(projectId: string) => Promise<ModelCatalogPage>>(
    fetchModelCatalog,
  );

export function ModelCatalogProvider({
  children,
  load,
}: {
  children: ReactNode;
  load: (projectId: string) => Promise<ModelCatalogPage>;
}) {
  return createElement(CatalogLoader.Provider, { value: load }, children);
}

export function useModelCatalog(projectId: string | null) {
  const load = useContext(CatalogLoader);
  return useQuery({
    queryKey: ["model-catalog", projectId],
    queryFn: () => load(projectId!),
    enabled: projectId !== null,
    retry: false,
    staleTime: 60000,
  });
}
