import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";
import { FWD_REPRESENTATIVE_EDGES } from "./fwdKnowledge";

export type GraphCommunity =
  | "sources"
  | "application"
  | "underwriting"
  | "parties"
  | "testing"
  | "new-business"
  | "servicing"
  | "claims"
  | "codebase";
export type GraphNodeKind = "document" | "concept" | "entity";
export type GraphProvenance = "extracted" | "inferred";
export interface GraphNode {
  community: GraphCommunity;
  degree: number;
  id: string;
  kind: GraphNodeKind;
  label: string;
  secondary?: string;
  x: number;
  y: number;
}
export interface GraphEdge {
  id: string;
  label: string;
  provenance: GraphProvenance;
  source: string;
  target: string;
}
export const GRAPH_WIDTH = 1560;
export const GRAPH_HEIGHT = 1120;
export const COMMUNITY_ORDER: readonly GraphCommunity[] = [
  "sources",
  "new-business",
  "application",
  "underwriting",
  "servicing",
  "parties",
  "claims",
  "codebase",
  "testing",
];
// Shared categorical palette: swatches, nodes and edges use the same topic color.
export const COMMUNITY_COLORS: Record<GraphCommunity, string> = {
  sources: "#64748b",
  "new-business": "#2563eb",
  application: "#6366f1",
  underwriting: "#0d9488",
  servicing: "#d97706",
  parties: "#b86b48",
  claims: "#e05b73",
  codebase: "#8b5cf6",
  testing: "#0891b2",
};
export const GRAPH_CLUSTERS = [
  { community: "new-business", x: 250, y: 200 },
  { community: "application", x: 780, y: 200 },
  { community: "underwriting", x: 1310, y: 200 },
  { community: "servicing", x: 250, y: 560 },
  { community: "parties", x: 780, y: 560 },
  { community: "claims", x: 1310, y: 560 },
  { community: "codebase", x: 400, y: 925 },
  { community: "testing", x: 1130, y: 925 },
] as const;

// Curated prototype relationships; these are not results of document extraction.
export function buildKnowledgeGraph(
  copy: PrototypeCopy,
  sources: readonly LibrarySource[],
) {
  const c = copy.library;
  const nodes: GraphNode[] = [
    ["application", c.application, "application", "concept", 325, 235],
    ["policy", c.policy, "application", "entity", 225, 155],
    ["coverage", c.coverage, "application", "entity", 445, 150],
    ["premium", c.premium, "application", "entity", 440, 325],
    [
      "health-disclosure",
      c.healthDisclosure,
      "underwriting",
      "concept",
      775,
      245,
    ],
    ["underwriting", c.underwriting, "underwriting", "concept", 885, 170],
    ["risk-assessment", c.riskAssessment, "underwriting", "entity", 990, 295],
    ["beneficiary", c.beneficiary, "parties", "concept", 325, 610],
    ["applicant", c.applicant, "parties", "entity", 230, 510],
    ["approval", c.approval, "parties", "concept", 440, 530],
    ["allocation", c.allocation, "parties", "entity", 435, 705],
    ["test-cases", c.testCasesNode, "testing", "concept", 820, 545],
    ["exploration", c.exploration, "testing", "concept", 1000, 530],
    ["execution", c.executionNode, "testing", "entity", 775, 680],
    ["defect", c.defect, "testing", "entity", 975, 685],
  ].map(([id, label, community, kind, x, y]) => ({
    id,
    label,
    community,
    kind,
    x,
    y,
    degree: 0,
  })) as GraphNode[];
  const previousCenters: Record<string, { x: number; y: number }> = {
    application: { x: 325, y: 225 },
    underwriting: { x: 875, y: 225 },
    parties: { x: 325, y: 615 },
    testing: { x: 875, y: 615 },
  };
  for (const node of nodes) {
    const previous = previousCenters[node.community]!;
    const cluster = GRAPH_CLUSTERS.find(
      (cluster) => cluster.community === node.community,
    )!;
    node.x += cluster.x - previous.x;
    node.y += cluster.y - previous.y;
  }
  for (const [id, label, community] of [
    ["new-business", c.newBusinessCommunity, "new-business"],
    ["policy-servicing", c.servicingCommunity, "servicing"],
    ["claims", c.claimsCommunity, "claims"],
    ["codebase", c.codebaseCommunity, "codebase"],
  ] as const) {
    const cluster = GRAPH_CLUSTERS.find(
      (cluster) => cluster.community === community,
    )!;
    nodes.push({
      id,
      label,
      community,
      kind: "concept",
      degree: 0,
      x: cluster.x,
      y: cluster.y,
    });
  }
  const base: [string, string, string, string, GraphProvenance][] = [
    [
      "new-business-application",
      "new-business",
      "application",
      c.creates,
      "inferred",
    ],
    ["servicing-policy", "policy-servicing", "policy", c.affects, "inferred"],
    [
      "servicing-beneficiary",
      "policy-servicing",
      "beneficiary",
      c.supports,
      "inferred",
    ],
    ["claims-coverage", "claims", "coverage", c.requires, "inferred"],
    ["claims-beneficiary", "claims", "beneficiary", c.informs, "inferred"],
    [
      "codebase-servicing",
      "codebase",
      "policy-servicing",
      c.supports,
      "inferred",
    ],
    ["test-codebase", "test-cases", "codebase", c.validates, "inferred"],
    [
      "application-health",
      "application",
      "health-disclosure",
      c.requires,
      "extracted",
    ],
    [
      "health-underwriting",
      "health-disclosure",
      "underwriting",
      c.informs,
      "extracted",
    ],
    [
      "application-beneficiary",
      "application",
      "beneficiary",
      c.names,
      "extracted",
    ],
    [
      "applicant-application",
      "applicant",
      "application",
      c.submits,
      "extracted",
    ],
    ["application-policy", "application", "policy", c.creates, "inferred"],
    ["policy-coverage", "policy", "coverage", c.defines, "extracted"],
    [
      "coverage-underwriting",
      "coverage",
      "underwriting",
      c.informs,
      "extracted",
    ],
    [
      "underwriting-risk",
      "underwriting",
      "risk-assessment",
      c.evaluates,
      "inferred",
    ],
    ["risk-premium", "risk-assessment", "premium", c.determines, "inferred"],
    [
      "beneficiary-allocation",
      "beneficiary",
      "allocation",
      c.requires,
      "extracted",
    ],
    [
      "approval-beneficiary",
      "approval",
      "beneficiary",
      c.confirms,
      "extracted",
    ],
    ["test-allocation", "test-cases", "allocation", c.validates, "extracted"],
    ["test-health", "test-cases", "health-disclosure", c.validates, "inferred"],
    ["exploration-test", "exploration", "test-cases", c.extends, "inferred"],
    ["test-execution", "test-cases", "execution", c.drives, "extracted"],
    ["execution-defect", "execution", "defect", c.reveals, "extracted"],
    ["defect-approval", "defect", "approval", c.affects, "inferred"],
  ];
  const edges: GraphEdge[] = base.map(
    ([id, source, target, label, provenance]) => ({
      id,
      source,
      target,
      label,
      provenance,
    }),
  );
  const linksFor = (source: LibrarySource): [string, string][] => {
    const representativeLinks: Record<string, [string, string][]> = {
      "fwd-product-vprime": [
        ["coverage", c.defines],
        ["policy", c.describes],
      ],
      "fwd-nb-issue": [
        ["new-business", c.describes],
        ["policy", c.creates],
      ],
      "fwd-ps-beneficiary": [
        ["policy-servicing", c.describes],
        ["approval", c.requires],
      ],
      "fwd-cl-medical": [
        ["claims", c.describes],
        ["risk-assessment", c.informs],
      ],
      "fwd-system-policy": [
        ["codebase", c.describes],
        ["policy", c.supports],
      ],
      "fwd-code-beneficiary": [
        ["codebase", c.describes],
        ["beneficiary", c.supports],
      ],
      "fwd-test-beneficiary-retry": [["test-cases", c.describes]],
      "fwd-automation-beneficiary": [["execution", c.drives]],
      "fwd-run-servicing": [["execution", c.records]],
      "fwd-defect-duplicate": [["defect", c.describes]],
    };
    if (representativeLinks[source.id]) return representativeLinks[source.id]!;
    const name = source.name.toLowerCase();
    if (/health|disclosure/.test(name))
      return [
        ["health-disclosure", c.supports],
        ["underwriting", c.informs],
      ];
    if (/underwriting/.test(name))
      return [
        ["underwriting", c.supports],
        ["risk-assessment", c.defines],
      ];
    if (/workflow/.test(name))
      return [
        ["beneficiary", c.describes],
        ["approval", c.defines],
      ];
    if (/test cases|test data|test-cases|test-data/.test(name))
      return [
        ["test-cases", c.defines],
        ["allocation", c.validates],
      ];
    if (/log|report/.test(name))
      return [
        ["execution", c.records],
        ["defect", c.reveals],
      ];
    if (/config/.test(name))
      return [
        ["execution", c.configures],
        ["test-cases", c.drives],
      ];
    if (/explorat|briefing|checklist/.test(name))
      return [
        ["exploration", c.supports],
        ["test-cases", c.extends],
      ];
    if (/beneficiar/.test(name))
      return [
        ["beneficiary", c.supports],
        ["allocation", c.describes],
      ];
    return [["application", c.supports]];
  };
  const grouped = GRAPH_CLUSTERS.map((cluster) => ({
    ...cluster,
    sources: sources.filter(
      (source) =>
        nodes.find((n) => n.id === linksFor(source)[0]![0])!.community ===
        cluster.community,
    ),
  }));
  for (const cluster of grouped)
    cluster.sources.forEach((source, index) => {
      const angle =
        -Math.PI * 0.85 +
        (index / Math.max(cluster.sources.length, 1)) * Math.PI * 2;
      nodes.push({
        id: `source-${source.id}`,
        label: source.name,
        secondary: source.type,
        community: source.id.startsWith("fwd-") ? cluster.community : "sources",
        kind: "document",
        degree: 0,
        x:
          cluster.x +
          (cluster.community === "testing" ? 270 : 190) * Math.cos(angle),
        y: cluster.y + 135 * Math.sin(angle),
      });
      linksFor(source).forEach(([target, label], i) =>
        edges.push({
          id: `document-${source.id}-${i}`,
          source: `source-${source.id}`,
          target,
          label,
          provenance: "extracted",
        }),
      );
    });
  const sourceIds = new Set(sources.map((source) => source.id));
  for (const edge of FWD_REPRESENTATIVE_EDGES) {
    if (
      !sourceIds.has(`fwd-${edge.source}`) ||
      !sourceIds.has(`fwd-${edge.target}`)
    )
      continue;
    edges.push({
      id: `fwd-${edge.id}`,
      source: `source-fwd-${edge.source}`,
      target: `source-fwd-${edge.target}`,
      label: edge.relation,
      provenance: "inferred",
    });
  }
  // Separate nearby file markers without moving the domain anchors.
  for (let pass = 0; pass < 160; pass++) {
    for (let i = 0; i < nodes.length; i++)
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]!,
          b = nodes[j]!;
        const dx = b.x - a.x,
          dy = b.y - a.y;
        const distance = Math.hypot(dx, dy);
        const min = 68;
        if (distance >= min || (a.kind !== "document" && b.kind !== "document"))
          continue;
        const ux = distance ? dx / distance : 1,
          uy = distance ? dy / distance : 0;
        const shift = (min - distance) * 0.52;
        if (a.kind === "document") {
          a.x -= ux * shift;
          a.y -= uy * shift;
        }
        if (b.kind === "document") {
          b.x += ux * shift;
          b.y += uy * shift;
        }
      }
    for (const node of nodes.filter((n) => n.kind === "document")) {
      // File markers must also clear the labels beneath domain nodes.
      for (const anchor of nodes.filter((n) => n.kind !== "document")) {
        const halfWidth = Math.min(anchor.label.length * 5, 145) + 27;
        const dx = node.x - anchor.x;
        const dy = node.y - (anchor.y + 50);
        if (Math.abs(dx) < halfWidth && Math.abs(dy) < 32) {
          if (halfWidth - Math.abs(dx) < 32 - Math.abs(dy))
            node.x = anchor.x + Math.sign(dx || 1) * (halfWidth + 1);
          else node.y = anchor.y + 50 + Math.sign(dy || 1) * 33;
        }
      }
      node.x = Math.max(40, Math.min(GRAPH_WIDTH - 40, node.x));
      node.y = Math.max(60, Math.min(GRAPH_HEIGHT - 40, node.y));
    }
  }
  for (const edge of edges) {
    nodes.find((n) => n.id === edge.source)!.degree++;
    nodes.find((n) => n.id === edge.target)!.degree++;
  }
  return { nodes, edges };
}
