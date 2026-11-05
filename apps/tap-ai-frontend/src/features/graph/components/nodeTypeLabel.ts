// `import type` only: dependency-cruiser's `features-do-not-import-upward`
// rule (features/ may not import widgets/) does not flag type-only imports,
// only value imports — see the identical note in `CommunityList.tsx`. This
// module never imports a `widgets/` *value*.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

/**
 * Localizes a raw node-type code (e.g. `"CONCEPT"`) using the copy's
 * `nodeTypes` map, falling back to the raw code for any type the map does
 * not cover — the zh copy must never surface a raw code, so every known
 * type is mapped, and only a genuinely unknown type falls through.
 */
export function nodeTypeLabel(copy: WorkspaceCopy, nodeType: string): string {
  return (
    copy.library.nodeTypes[nodeType as keyof typeof copy.library.nodeTypes] ??
    nodeType
  );
}
