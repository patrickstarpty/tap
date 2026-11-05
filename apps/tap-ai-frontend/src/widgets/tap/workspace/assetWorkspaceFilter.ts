// Used by AgentsWorkspace to facet its catalog (All / Built-in / Custom /
// Disabled); kept generic over items that only differ in which extra
// fields they carry. The Skills page filters on the server instead.

export type AssetWorkspaceFilter = "all" | "built-in" | "custom" | "disabled";

export interface FilterableAssetItem {
  sourceKind: string;
  status: string;
}

/** "Built-in"/"Custom" are origin facets restricted to enabled items;
 * "Disabled" is a status facet spanning both origins. Each item lands in
 * exactly one of the three facets, matching the four-state filter the
 * brief specifies (All / Built-in / Custom / Disabled). */
export function filterAssetWorkspaceItems<T extends FilterableAssetItem>(
  items: readonly T[],
  filter: AssetWorkspaceFilter,
): T[] {
  if (filter === "built-in")
    return items.filter(
      (item) => item.sourceKind === "built_in" && item.status === "enabled",
    );
  if (filter === "custom")
    return items.filter(
      (item) => item.sourceKind !== "built_in" && item.status === "enabled",
    );
  if (filter === "disabled")
    return items.filter((item) => item.status === "disabled");
  return [...items];
}
