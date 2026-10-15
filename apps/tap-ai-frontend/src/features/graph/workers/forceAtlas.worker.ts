import type { GraphEdge } from "../model/graph";
import { computeForceAtlasPositions } from "./forceAtlasLayout";

self.onmessage = (
  event: MessageEvent<{
    nodes: { id: string; communityId: string }[];
    edges: GraphEdge[];
    communityOrder: string[];
    reducedMotion: boolean;
  }>,
) => {
  const positions = computeForceAtlasPositions(
    event.data.nodes,
    event.data.edges,
    event.data.communityOrder,
    event.data.reducedMotion,
  );
  self.postMessage(positions);
};
