import type { components } from "../../../shared/api/generated/schema";

export type RelationCitation = components["schemas"]["RelationCitationView"];

export function relationGraphUrl(citation: RelationCitation): string {
  const url = new URL(window.location.href);
  url.searchParams.set("module", "library");
  url.searchParams.set("snapshot", citation.snapshotId);
  url.searchParams.set(
    "highlight",
    [...new Set([citation.edgeId, ...citation.pathEdgeIds])].join(","),
  );
  return `${url.pathname}${url.search}${url.hash}`;
}

export function relationGraphSelection(): {
  snapshotId: string;
  edgeIds: string[];
} | null {
  const params = new URLSearchParams(window.location.search);
  const snapshotId = params.get("snapshot");
  const edgeIds = [
    ...new Set((params.get("highlight") ?? "").split(",").filter(Boolean)),
  ];
  return snapshotId && edgeIds.length > 0 ? { snapshotId, edgeIds } : null;
}
