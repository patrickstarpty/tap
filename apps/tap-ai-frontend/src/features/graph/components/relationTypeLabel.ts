// `import type` only: dependency-cruiser's `features-do-not-import-upward`
// rule (features/ may not import widgets/) does not flag type-only imports,
// only value imports — see the identical note in `CommunityList.tsx`. This
// module never imports a `widgets/` *value*.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

/**
 * Localizes a raw relation-type code (e.g. `"REQUIRES"`) using the copy's
 * `relationTypes` map, falling back to the raw code for any type the map
 * does not cover — the zh copy must never surface a raw code, so every
 * vocabulary type is mapped, and only a genuinely unknown type falls
 * through.
 */
export function relationTypeLabel(
  copy: WorkspaceCopy,
  relationType: string,
): string {
  return (
    copy.library.relationTypes[
      relationType as keyof typeof copy.library.relationTypes
    ] ?? relationType
  );
}
