import products from "./fwdProducts.json";
import type { LibrarySource } from "./model";

export type FwdGroup =
  | "products"
  | "new-business"
  | "servicing"
  | "claims"
  | "systems"
  | "code"
  | "tests"
  | "automation"
  | "evidence";
export type FwdKind =
  | "product"
  | "topic"
  | "process"
  | "rule"
  | "system"
  | "code"
  | "test"
  | "automation"
  | "execution"
  | "defect";
export interface FwdNode {
  id: string;
  label: string;
  group: FwdGroup;
  kind: FwdKind;
  provenance: "public" | "demo";
  content: string;
  sourceUrl?: string;
  path?: string;
  category?: string;
  hub?: boolean;
}
export interface FwdEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  provenance: "public" | "demo";
}
export interface FwdData {
  nodes: FwdNode[];
  edges: FwdEdge[];
}
export const FWD_REVIEW_DATE = "2026-09-06";
export const FWD_GROUPS: {
  id: FwdGroup;
  en: string;
  zh: string;
  color: string;
  x: number;
  y: number;
}[] = [
  {
    id: "products",
    en: "Products",
    zh: "产品",
    color: "#b27632",
    x: 760,
    y: 160,
  },
  {
    id: "new-business",
    en: "New business",
    zh: "新业务",
    color: "#6481b3",
    x: 280,
    y: 455,
  },
  {
    id: "servicing",
    en: "Policy servicing",
    zh: "保单服务",
    color: "#26978b",
    x: 760,
    y: 465,
  },
  { id: "claims", en: "Claims", zh: "理赔", color: "#ac758d", x: 1240, y: 455 },
  {
    id: "systems",
    en: "Systems",
    zh: "系统",
    color: "#8276b2",
    x: 290,
    y: 805,
  },
  { id: "code", en: "Codebase", zh: "代码", color: "#657e95", x: 745, y: 815 },
  {
    id: "tests",
    en: "Test cases",
    zh: "测试用例",
    color: "#518ba0",
    x: 1230,
    y: 805,
  },
  {
    id: "automation",
    en: "Automation",
    zh: "自动化",
    color: "#7d985a",
    x: 505,
    y: 1140,
  },
  {
    id: "evidence",
    en: "Runs & defects",
    zh: "执行与缺陷",
    color: "#b77b63",
    x: 1005,
    y: 1140,
  },
];
const publicSupport = "https://www.fwd.com.hk/online-insurance/support/en/";
const support = "https://www.fwd.com.hk/en/support/";
const claims = "https://www.fwd.com.hk/en/claims/";
const demoNotice =
  "TAP demonstration model. Not FWD internal documentation, source code or production behavior. No real customer data. ";
const nodes: FwdNode[] = [];
const edges: FwdEdge[] = [];
function add(node: FwdNode) {
  nodes.push(node);
}
function link(
  source: string,
  target: string,
  relation: string,
  provenance: FwdEdge["provenance"] = "demo",
) {
  edges.push({
    id: `${source}:${relation}:${target}`,
    source,
    target,
    relation,
    provenance,
  });
}
const families: Record<string, string> = {
  savings: "Savings & retirement",
  "life-protection": "Life protection",
  "critical-illness": "Critical illness",
  "medical-cover": "Medical cover",
  vhis: "VHIS",
  general: "General insurance",
  accident: "Accident protection",
};
for (const [id, label] of Object.entries(families)) {
  add({
    id: `family-${id}`,
    label,
    group: "products",
    kind: "topic",
    hub: true,
    provenance: "public",
    sourceUrl: "https://www.fwd.com.hk/en/products/",
    content: `Public catalogue grouping: ${label}. Product and series names were checked against the FWD HK public directory on ${FWD_REVIEW_DATE}. Grouping is curated for browsing; it is not a sales eligibility decision.`,
  });
}
const online = [
  [
    "myterm-plus",
    "MyTerm Plus Term Life Insurance Plan",
    "life-protection",
    "term-life",
  ],
  [
    "mycover",
    "MyCover Critical Illness Plan",
    "critical-illness",
    "term-critical-illness",
  ],
  ["mymillion", "MyMillion Medical Plan", "medical-cover", "mymillion-medical"],
  [
    "healthy-plus",
    "Healthy Plus Refundable Hospital Income Plan",
    "medical-cover",
    "healthy-plus",
  ],
  [
    "mysafe",
    "MySafe Accident Protection Plan",
    "accident",
    "mysafe-accident-protection-plan",
  ],
].map(([id, name, category, path]) => ({
  id: id!,
  name: name!,
  category: category!,
  url: `https://www.fwd.com.hk/online-insurance/${path}/en/`,
  listedOn: "https://www.fwd.com.hk/online-insurance/en/",
  availability:
    "Online product referenced by the official online catalogue; this may be a version within a series, not an additional distinct insurance contract.",
}));
for (const p of [...products, ...online]) {
  add({
    id: `product-${p.id}`,
    label: p.name,
    group: "products",
    kind: "product",
    category: p.category,
    provenance: "public",
    sourceUrl: p.url,
    path: `products/${p.id}.md`,
    content: `# ${p.name}\n\nPublic product / series reference · reviewed ${FWD_REVIEW_DATE}\n\nCategory: ${families[p.category]}\nSource: ${p.url}\nDirectory: ${p.listedOn}\n\n${p.availability}\n\nThis record indexes the product and its public page. It does not reproduce policy wording or confirm suitability, eligibility, exclusions, rates or benefits. Lifecycle and software links in this demo are illustrative mappings, not verified product-specific processing rules.`,
  });
  link(`family-${p.category}`, `product-${p.id}`, "includes", "public");
}
link("product-myterm", "product-myterm-plus", "online version", "public");
const domains = [
  [
    "new-business",
    "New business",
    publicSupport,
    "Public online support describes application confirmation, identity documents, payment and access to issued policies. The detailed stages below are a demonstration decomposition, except where a public reference is explicitly attached.",
  ],
  [
    "servicing",
    "Policy servicing",
    support,
    "FWD public customer support offers policy information, beneficiary and contact changes, payment options and investment instructions. Detailed validation, approval and system design in this graph is illustrative.",
  ],
  [
    "claims",
    "Claims",
    claims,
    "The public claims guide branches by policy and claim type and asks the customer to prepare the relevant documents. This graph models intake, review and settlement for demonstration; no claims decision or service promise is implied.",
  ],
] as const;
for (const [id, label, url, content] of domains)
  add({
    id: `domain-${id}`,
    label,
    group: id,
    kind: "topic",
    hub: true,
    provenance: "public",
    sourceUrl: url,
    content,
  });
for (const p of [...products, ...online]) {
  link(`product-${p.id}`, "domain-new-business", "application journey");
  link(`product-${p.id}`, "domain-servicing", "policy lifecycle");
  link(`product-${p.id}`, "domain-claims", "claim context");
}
// Public facts are deliberately small; detailed operating rules remain explicit demo assumptions.
const processes: [string, string, FwdGroup, string, string?][] = [
  [
    "nb-needs",
    "Needs & product selection",
    "new-business",
    "Select a product and record applicant needs. Demo review captures affordability and channel context without inventing product eligibility thresholds.",
  ],
  [
    "nb-identity",
    "Identity & consent",
    "new-business",
    "The online application FAQ asks for an HKID card and a credit card. The demo verifies the presence of identity evidence and consent; it does not perform real identity verification.",
    publicSupport,
  ],
  [
    "nb-disclosure",
    "Health declaration",
    "new-business",
    "The online FAQ directs applicants with uncertain health conditions to an adviser. The demo preserves the original answer and routes uncertainty for review, with no medical underwriting decision.",
    publicSupport,
  ],
  [
    "nb-underwriting",
    "Underwriting review",
    "new-business",
    "Demo workflow: a reviewer inspects declared information, requests missing evidence and records a reasoned decision. No FWD underwriting manual or actual decision rules were supplied.",
  ],
  [
    "nb-payment",
    "Initial premium payment",
    "new-business",
    "The online FAQ mentions credit-card payment during application. Duplicate payment callbacks and delayed authorisation are demo engineering scenarios.",
    publicSupport,
  ],
  [
    "nb-issue",
    "Policy issuance",
    "new-business",
    "The public FAQ says submitted applications receive confirmation with a reference number and issued policy documents can be viewed in eServices. Confirmation is not modeled as automatic acceptance.",
    publicSupport,
  ],
  [
    "nb-delivery",
    "Policy delivery & acknowledgement",
    "new-business",
    "The public support page lists acknowledgement of policy receipt. Demo records document version, receipt timestamp and customer acknowledgement separately.",
    publicSupport,
  ],
  [
    "ps-beneficiary",
    "Change beneficiary",
    "servicing",
    "FWD public support lists beneficiary change as an eServices function. Allocation totals, stale-version handling, approval and idempotency in this graph are demonstration assumptions, not published FWD rules.",
    publicSupport,
  ],
  [
    "ps-contact",
    "Update contact information",
    "servicing",
    "FWD public support lists contact-information changes. Demo tests validation, verification and protection against cross-policy access.",
    publicSupport,
  ],
  [
    "ps-payment",
    "Change payment option",
    "servicing",
    "FWD public support lists changing payment options. The demo models scheduling, mandate state and a duplicate-update guard.",
    publicSupport,
  ],
  [
    "ps-statement",
    "Policy documents & statements",
    "servicing",
    "Issued policy documents can be checked through eServices. The demo authorises each document download against its policy owner.",
    publicSupport,
  ],
  [
    "ps-investment",
    "Switch investment instruction",
    "servicing",
    "The public support page lists switching or changing investment instructions. This feature is not assumed to apply to every product in the catalogue.",
    support,
  ],
  [
    "ps-withdrawal",
    "Withdrawal request",
    "servicing",
    "Demonstration of a product-dependent withdrawal request. Validate authority, currency, available value and a versioned quotation; no actual product withdrawal value is supplied.",
  ],
  [
    "ps-reinstatement",
    "Reinstatement review",
    "servicing",
    "Demonstration of a lapsed-policy review: gather required evidence, create a review task and preserve prior status until approval. Not a promise of reinstatement eligibility.",
  ],
  [
    "cl-intake",
    "Claim intake",
    "claims",
    "The public claims guide starts with selection of policy and claim type. Demo records the claim context without changing a policy balance.",
    claims,
  ],
  [
    "cl-medical",
    "Medical claim documents",
    "claims",
    "The claims guide separates medical claim paths. Use the current official path to identify required evidence. Receipt completeness and upload retry are demo test scenarios.",
    claims,
  ],
  [
    "cl-critical",
    "Critical illness claim",
    "claims",
    "The official claims selector includes critical illness. Exact diagnoses, definitions and exclusions remain governed by the applicable policy; the demo tests routing and evidence completeness only.",
    claims,
  ],
  [
    "cl-death",
    "Death claim",
    "claims",
    "The official claims selector includes death claims. The demo models claimant authority, required evidence review and an auditable decision; no real entitlement calculation is represented.",
    claims,
  ],
  [
    "cl-accident",
    "Accident claim",
    "claims",
    "Accident claim paths appear in the official claims selector. The demo distinguishes expense, disability and death contexts before requesting evidence.",
    claims,
  ],
  [
    "cl-coverage",
    "Coverage & policy check",
    "claims",
    "Demo validates policy identity and sends effective-date and coverage questions to a versioned review service. It does not implement a real FWD coverage determination.",
  ],
  [
    "cl-assessment",
    "Claim assessment",
    "claims",
    "Demo reviewer records evidence, an assessment reason and the applicable version of a decision rule. An incomplete evidence package stays pending.",
  ],
  [
    "cl-settlement",
    "Settlement & reconciliation",
    "claims",
    "Demo settlement uses one payment instruction per approved claim and reconciles a callback before marking it paid. Execution results shown are fictional.",
  ],
  [
    "cl-appeal",
    "Additional evidence & appeal",
    "claims",
    "Demo reopens an evidence review without overwriting the original decision or audit trail. Appeals policies and legal deadlines are not supplied.",
  ],
];
for (const [id, label, group, content, url] of processes) {
  add({
    id,
    label,
    group,
    kind: "process",
    provenance: url ? "public" : "demo",
    sourceUrl: url,
    path: `business/${id}.md`,
    content: url ? content : demoNotice + content,
  });
  link(`domain-${group}`, id, "contains stage");
}
for (const sequence of [
  [
    "nb-needs",
    "nb-identity",
    "nb-disclosure",
    "nb-underwriting",
    "nb-payment",
    "nb-issue",
    "nb-delivery",
  ],
  [
    "cl-intake",
    "cl-medical",
    "cl-coverage",
    "cl-assessment",
    "cl-settlement",
    "cl-appeal",
  ],
])
  for (let i = 1; i < sequence.length; i++)
    link(sequence[i - 1]!, sequence[i]!, "precedes");
link("nb-issue", "ps-statement", "produces");
link("ps-beneficiary", "cl-death", "affects claimant context");
link("nb-disclosure", "cl-assessment", "provides declared evidence");
link("ps-payment", "nb-payment", "shares payment mandate");
for (const id of [
  "vprime",
  "vfamily",
  "vcare",
  "one-n-all-medical-insurance-plan",
])
  link(`product-${id}`, "cl-medical", "demo claim route");
link("product-mycover", "cl-critical", "demo claim route");
link("product-myterm-plus", "cl-death", "demo claim route");
link("product-mysafe", "cl-accident", "demo claim route");
const systems: [string, string, string, string[]][] = [
  [
    "portal",
    "Customer portal",
    "Session-scoped customer journeys; view policies, submit requests and inspect statuses.",
    ["nb-needs", "ps-contact", "cl-intake"],
  ],
  [
    "identity",
    "Identity & access",
    "Policy ownership and reviewer permissions; no real authentication provider is connected.",
    ["nb-identity", "ps-statement", "cl-death"],
  ],
  [
    "application",
    "Application service",
    "Versioned applications with separate submitted, review and issued states.",
    ["nb-disclosure", "nb-issue"],
  ],
  [
    "underwriting",
    "Underwriting workbench",
    "Review tasks and evidence requests; all decision rules are demonstration assumptions.",
    ["nb-underwriting", "nb-disclosure"],
  ],
  [
    "policy",
    "Policy administration",
    "Policy versions, service requests and beneficiary change history.",
    ["ps-beneficiary", "ps-reinstatement", "ps-withdrawal"],
  ],
  [
    "billing",
    "Billing & collection",
    "Premium requests, mandate updates and idempotent callback reconciliation.",
    ["nb-payment", "ps-payment"],
  ],
  [
    "documents",
    "Document service",
    "Versioned documents, content checksums and authorised retrieval.",
    ["ps-statement", "cl-medical", "nb-delivery"],
  ],
  [
    "claims",
    "Claims workbench",
    "Claim routing, evidence completeness and review transitions.",
    ["cl-intake", "cl-assessment", "cl-coverage"],
  ],
  [
    "settlement",
    "Settlement adapter",
    "One instruction per approved claim, reconciliation and audit entries.",
    ["cl-settlement"],
  ],
  [
    "investment",
    "Investment servicing",
    "A fictional instruction queue; no orders are submitted to a real provider.",
    ["ps-investment"],
  ],
  [
    "notification",
    "Notification service",
    "Notifications reference the request version and support safe retries.",
    ["nb-delivery", "ps-contact", "cl-appeal"],
  ],
  [
    "audit",
    "Audit & event store",
    "Correlated events with request identifiers and redacted payloads.",
    ["ps-beneficiary", "cl-settlement", "nb-issue"],
  ],
];
for (const [id, label, content, processIds] of systems) {
  add({
    id: `system-${id}`,
    label,
    group: "systems",
    kind: "system",
    hub: true,
    provenance: "demo",
    path: `architecture/${id}.md`,
    content:
      demoNotice +
      content +
      "\n\nBoundary: this is an illustrative module, not a discovered FWD repository or vendor platform.\nData: synthetic references only.\nObservability: correlate requestId, policyId and revision; redact customer information.",
  });
  for (const process of processIds) link(process, `system-${id}`, "served by");
}
for (const [a, b] of [
  ["portal", "identity"],
  ["application", "underwriting"],
  ["application", "billing"],
  ["application", "policy"],
  ["policy", "documents"],
  ["claims", "policy"],
  ["claims", "documents"],
  ["claims", "settlement"],
  ["settlement", "billing"],
  ["notification", "audit"],
  ["policy", "audit"],
])
  link(`system-${a}`, `system-${b}`, "calls");
interface Scenario {
  key: string;
  label: string;
  process: string;
  system: string;
  field: string;
  valid: unknown;
  invalid: unknown;
  expected: string;
}
const scenarios: Scenario[] = [
  {
    key: "identity",
    label: "Applicant evidence",
    process: "nb-identity",
    system: "identity",
    field: "consent",
    valid: true,
    invalid: false,
    expected: "Reject missing consent without advancing the application.",
  },
  {
    key: "disclosure",
    label: "Uncertain health answer",
    process: "nb-disclosure",
    system: "application",
    field: "reviewRequired",
    valid: true,
    invalid: null,
    expected:
      "Preserve the original disclosure and require a reviewer; never auto-approve uncertainty.",
  },
  {
    key: "underwriting",
    label: "Underwriting decision",
    process: "nb-underwriting",
    system: "underwriting",
    field: "evidenceVersion",
    valid: 3,
    invalid: null,
    expected:
      "Reject a decision without an evidence version and keep review pending.",
  },
  {
    key: "premium",
    label: "Premium callback",
    process: "nb-payment",
    system: "billing",
    field: "paymentReference",
    valid: "demo-pay-001",
    invalid: "",
    expected:
      "Reject an empty reference and do not issue a policy before reconciliation.",
  },
  {
    key: "issuance",
    label: "Policy issue transition",
    process: "nb-issue",
    system: "application",
    field: "approved",
    valid: true,
    invalid: false,
    expected: "An unapproved application must remain unissued.",
  },
  {
    key: "delivery",
    label: "Policy acknowledgement",
    process: "nb-delivery",
    system: "documents",
    field: "documentVersion",
    valid: 2,
    invalid: 0,
    expected: "Record acknowledgement only for the delivered document version.",
  },
  {
    key: "beneficiary",
    label: "Beneficiary allocation",
    process: "ps-beneficiary",
    system: "policy",
    field: "allocationTotal",
    valid: 100,
    invalid: 99,
    expected:
      "Demo rule: allocations must total 100; reject 99 or 101 without changing the policy.",
  },
  {
    key: "contact",
    label: "Contact change",
    process: "ps-contact",
    system: "portal",
    field: "verified",
    valid: true,
    invalid: false,
    expected:
      "An unverified update must not replace the existing contact record.",
  },
  {
    key: "mandate",
    label: "Payment mandate",
    process: "ps-payment",
    system: "billing",
    field: "mandateStatus",
    valid: "active",
    invalid: "revoked",
    expected: "Do not schedule collection against a revoked mandate.",
  },
  {
    key: "document",
    label: "Policy document access",
    process: "ps-statement",
    system: "documents",
    field: "ownsPolicy",
    valid: true,
    invalid: false,
    expected:
      "Deny another policyholder's document and emit no document bytes.",
  },
  {
    key: "investment",
    label: "Investment instruction",
    process: "ps-investment",
    system: "investment",
    field: "instructionTotal",
    valid: 100,
    invalid: 101,
    expected:
      "Demo rule: allocation must total 100 before queuing the instruction.",
  },
  {
    key: "withdrawal",
    label: "Withdrawal quotation",
    process: "ps-withdrawal",
    system: "policy",
    field: "quoteVersion",
    valid: 4,
    invalid: 3,
    expected:
      "Reject an outdated quotation; ask for a fresh one before approval.",
  },
  {
    key: "reinstatement",
    label: "Reinstatement request",
    process: "ps-reinstatement",
    system: "policy",
    field: "reviewApproved",
    valid: true,
    invalid: false,
    expected: "Keep the policy lapsed until the review is approved.",
  },
  {
    key: "intake",
    label: "Claim registration",
    process: "cl-intake",
    system: "claims",
    field: "policyReference",
    valid: "demo-policy-001",
    invalid: "",
    expected: "Require a policy reference before routing the claim.",
  },
  {
    key: "medical",
    label: "Medical evidence",
    process: "cl-medical",
    system: "documents",
    field: "receiptAttached",
    valid: true,
    invalid: false,
    expected:
      "Keep missing-receipt claims pending evidence; do not infer an amount.",
  },
  {
    key: "critical",
    label: "Critical illness routing",
    process: "cl-critical",
    system: "claims",
    field: "claimType",
    valid: "critical-illness",
    invalid: "unknown",
    expected:
      "Route by claim type and do not substitute a medical-expense decision.",
  },
  {
    key: "death",
    label: "Claimant authority",
    process: "cl-death",
    system: "identity",
    field: "authorityReviewed",
    valid: true,
    invalid: false,
    expected:
      "Hold a death claim for review when claimant authority is unresolved.",
  },
  {
    key: "accident",
    label: "Accident claim context",
    process: "cl-accident",
    system: "claims",
    field: "accidentDate",
    valid: "2026-08-12",
    invalid: "",
    expected: "Require incident context before classifying the claim path.",
  },
  {
    key: "coverage",
    label: "Coverage version",
    process: "cl-coverage",
    system: "policy",
    field: "policyVersion",
    valid: 5,
    invalid: null,
    expected: "Never assess coverage against an unknown policy version.",
  },
  {
    key: "assessment",
    label: "Assessment evidence",
    process: "cl-assessment",
    system: "claims",
    field: "complete",
    valid: true,
    invalid: false,
    expected: "Do not approve a claim whose required evidence is incomplete.",
  },
  {
    key: "settlement",
    label: "Settlement approval",
    process: "cl-settlement",
    system: "settlement",
    field: "approved",
    valid: true,
    invalid: false,
    expected: "Never create a payment instruction for an unapproved claim.",
  },
  {
    key: "appeal",
    label: "Appeal audit history",
    process: "cl-appeal",
    system: "audit",
    field: "originalDecisionId",
    valid: "demo-decision-001",
    invalid: "",
    expected:
      "Preserve and reference the prior decision when new evidence is submitted.",
  },
];
for (const s of scenarios) {
  const codeId = `code-${s.key}`;
  const code = `// ${demoNotice}\n// Minimal contract sketch, not a deployed endpoint. In-memory state is illustrative.\n// Production requires authentication, durable transactions and concurrency control.\nconst responses = new Map<string, { status: string }>();\nexport function handle(input: { requestId: string; ${s.field}: unknown }) {\n  if (!input.requestId) throw new Error("request_id_required");\n  if (input.${s.field} !== ${JSON.stringify(s.valid)}) {\n    throw new Error("${s.key}_validation_failed");\n  }\n  const previous = responses.get(input.requestId);\n  if (previous) return previous;\n  const result = { status: "accepted_for_demo_review" };\n  responses.set(input.requestId, result);\n  return result;\n}\n`;
  add({
    id: codeId,
    label: `${s.key}.ts`,
    group: "code",
    kind: "code",
    provenance: "demo",
    path: `demo-insurance/services/${s.system}/${s.key}.ts`,
    content: code,
  });
  link(`system-${s.system}`, codeId, "contains file");
  link(s.process, codeId, "implemented by");
  const rules = [
    [
      "valid",
      "Valid input",
      JSON.stringify(s.valid),
      "Accept for demo review; this is not policy or claim approval.",
    ],
    [
      "boundary",
      "Invalid / boundary input",
      JSON.stringify(s.invalid),
      s.expected,
    ],
    [
      "retry",
      "Repeated submission",
      JSON.stringify(s.valid),
      "Use the same request identifier twice; return the same response and produce one mutation.",
    ],
  ];
  for (const [suffix, title, value, expected] of rules) {
    const id = `test-${s.key}-${suffix}`;
    add({
      id,
      label: `${s.label} · ${title}`,
      group: "tests",
      kind: "test",
      provenance: "demo",
      path: `test-cases/${s.key}-${suffix}.md`,
      content: `# ${s.label}: ${title}\n\n${demoNotice}\n\nPriority: ${suffix === "valid" ? "P1" : "P0"}\nPrecondition: authorised demo user and an isolated synthetic policy.\nInput: ${s.field} = ${value}\nSteps: open ${s.process}; submit the request${suffix === "retry" ? "; retry with the same requestId" : ""}; inspect response, stored revision and audit events.\nExpected: ${expected}\nEvidence: redacted response, before/after revision and correlated event IDs.\nStatus: designed; no real FWD execution performed.`,
    });
    link(id, s.process, "validates");
    link(id, codeId, "covers");
  }
  const autoId = `automation-${s.key}`;
  add({
    id: autoId,
    label: `${s.key}.spec.ts`,
    group: "automation",
    kind: "automation",
    provenance: "demo",
    path: `demo-insurance/tests/${s.key}.spec.ts`,
    content: `// ${demoNotice}\n// Executable unit-test sketch for the adjacent demo handler; not a FWD integration test.\nimport { expect, test } from "vitest";\nimport { handle } from "../services/${s.system}/${s.key}";\n\ntest("${s.label}: valid input", () => {\n  expect(handle({requestId:"demo-${s.key}-valid",${s.field}:${JSON.stringify(s.valid)}}).status).toBe("accepted_for_demo_review");\n});\ntest("${s.label}: invalid input", () => {\n  expect(() => handle({requestId:"demo-${s.key}-invalid",${s.field}:${JSON.stringify(s.invalid)}})).toThrow();\n});\ntest("${s.label}: repeated submission", () => {\n  const input = {requestId:"demo-${s.key}-retry",${s.field}:${JSON.stringify(s.valid)}};\n  expect(handle(input)).toBe(handle(input));\n});\n`,
  });
  for (const suffix of ["valid", "boundary", "retry"])
    link(`test-${s.key}-${suffix}`, autoId, "automated by");
  link(autoId, codeId, "imports");
}
const suites = [
  [
    "new-business",
    "New business regression",
    [
      "identity",
      "disclosure",
      "underwriting",
      "premium",
      "issuance",
      "delivery",
    ],
  ],
  [
    "servicing",
    "Policy servicing regression",
    [
      "beneficiary",
      "contact",
      "mandate",
      "document",
      "investment",
      "withdrawal",
      "reinstatement",
    ],
  ],
  [
    "claims",
    "Claims regression",
    [
      "intake",
      "medical",
      "critical",
      "death",
      "accident",
      "coverage",
      "assessment",
      "settlement",
      "appeal",
    ],
  ],
] as const;
for (const [id, label, keys] of suites) {
  add({
    id: `run-${id}`,
    label: `${label} · demo run`,
    group: "evidence",
    kind: "execution",
    provenance: "demo",
    path: `evidence/${id}-run.json`,
    content: JSON.stringify(
      {
        notice: demoNotice,
        executed: false,
        illustrative: true,
        runId: `demo-${id}-042`,
        suite: label,
        environment: "fictional-history",
        scripts: keys,
        scenarioCount: keys.length * 3,
        meaning:
          "An illustrative historical run record for graph exploration. It is not a result from running these downloaded snippets or any FWD environment.",
      },
      null,
      2,
    ),
  });
  for (const key of keys)
    link(`automation-${key}`, `run-${id}`, "recorded in demo run");
  add({
    id: `pipeline-${id}`,
    label: `${id}.workflow.yaml`,
    group: "automation",
    kind: "automation",
    provenance: "demo",
    path: `demo-insurance/pipelines/${id}.workflow.yaml`,
    content: `# ${demoNotice}\n# Pipeline design example only; install a test runner before adapting locally.\nname: ${label}\nenvironment: isolated-demo\nnetwork: loopback-only\nartifacts:\n  redact: [customer_name, identity_number, bank_account]\nscripts:\n${keys.map((k) => `  - tests/${k}.spec.ts`).join("\n")}\nquality_gate:\n  fail_on_failed_test: true\n  require_audit_evidence: true\n`,
  });
  link(`pipeline-${id}`, `run-${id}`, "orchestrates demo");
}
const defects = [
  [
    "duplicate",
    "Duplicate beneficiary audit event",
    "beneficiary",
    "servicing",
    "A fictional earlier implementation emitted two events after a timeout retry. Expected one mutation for one request identifier.",
  ],
  [
    "payment",
    "Repeated premium callback",
    "premium",
    "new-business",
    "A fictional callback created a second issuance event. Reconcile before issue and enforce a durable idempotency key.",
  ],
  [
    "disclosure",
    "Uncertain answer auto-approved",
    "disclosure",
    "new-business",
    "A fictional default converted an unanswered field to false. Preserve unknown as a distinct state and route for review.",
  ],
  [
    "document",
    "Cross-policy document reference",
    "document",
    "servicing",
    "A fictional document lookup checked existence but omitted ownership. Re-authorise every download.",
  ],
  [
    "receipt",
    "Receipt lost during upload retry",
    "medical",
    "claims",
    "A fictional retry overwrote an uploaded receipt reference. Preserve attachment identity and content checksum.",
  ],
  [
    "settlement",
    "Duplicate settlement instruction",
    "settlement",
    "claims",
    "A fictional settlement retry created a second payment instruction. Use an atomic unique claim-payment key.",
  ],
] as const;
for (const [id, label, key, suite, detail] of defects) {
  add({
    id: `defect-${id}`,
    label,
    group: "evidence",
    kind: "defect",
    provenance: "demo",
    path: `evidence/DEMO-${id}.md`,
    content: `# DEMO finding: ${label}\n\n${demoNotice}\n\n${detail}\n\nStatus: illustrative historical finding, not an observed FWD defect.\nInvestigation: reproduce in an isolated fixture, inspect the request ID and event count, add a regression test, and review transaction boundaries.\nThe adjacent code sketch illustrates one guard; it is not a production fix.`,
  });
  link(`run-${suite}`, `defect-${id}`, "illustrates finding");
  link(`defect-${id}`, `code-${key}`, "investigates");
  link(`defect-${id}`, `test-${key}-retry`, "motivates regression");
}
const ruleItems = [
  [
    "version",
    "Versioned business rules",
    "nb-underwriting",
    "Store the effective version with each decision. A current rule must not silently replace the rule used for a past decision.",
  ],
  [
    "authority",
    "Policyholder authority",
    "ps-beneficiary",
    "Check the acting user's authority against the policy on every service request; never trust a client-supplied role.",
  ],
  [
    "allocation",
    "Allocation total",
    "ps-beneficiary",
    "Demo-only constraint: beneficiary percentages total 100. Verify 99, 100 and 101 plus decimal rounding.",
  ],
  [
    "idempotency",
    "Idempotent mutation",
    "cl-settlement",
    "Retrying the same logical request must not create a second payment or mutation. In production, combine durable uniqueness and atomic writes.",
  ],
  [
    "evidence",
    "Evidence completeness",
    "cl-assessment",
    "Required evidence is explicit and versioned. Missing evidence yields pending review rather than automatic rejection or approval.",
  ],
  [
    "audit",
    "Immutable decision history",
    "cl-appeal",
    "Preserve the original reason, actor and evidence version when a subsequent review is opened.",
  ],
  [
    "currency",
    "Currency consistency",
    "ps-withdrawal",
    "Amounts carry an explicit currency. Do not silently mix a policy currency, payment currency and display currency.",
  ],
  [
    "privacy",
    "Redacted test evidence",
    "ps-statement",
    "Test fixtures use fictional references. Logs exclude identity numbers, customer names and account details.",
  ],
] as const;
for (const [id, label, process, detail] of ruleItems) {
  const group = processes.find((p) => p[0] === process)![2];
  add({
    id: `rule-${id}`,
    label,
    group,
    kind: "rule",
    provenance: "demo",
    path: `rules/${id}.md`,
    content: demoNotice + detail,
  });
  link(process, `rule-${id}`, "constrained by");
}
export const FWD_KNOWLEDGE: FwdData = { nodes, edges };
export function getFwdNeighborhood(
  data: FwdData,
  id: string,
  hops: number,
): Set<string> {
  if (!data.nodes.some((n) => n.id === id)) return new Set();
  const result = new Set([id]);
  let frontier = new Set([id]);
  for (let step = 0; step < hops; step++) {
    const next = new Set<string>();
    for (const e of data.edges) {
      if (frontier.has(e.source) && !result.has(e.target)) next.add(e.target);
      if (frontier.has(e.target) && !result.has(e.source)) next.add(e.source);
    }
    for (const id of next) result.add(id);
    frontier = next;
  }
  return result;
}
export const FWD_SOURCES: readonly LibrarySource[] = nodes
  .filter((n) => n.path)
  .map((n) => ({
    id: `fwd-${n.id}`,
    name: n.path!.split("/").pop()!,
    type: n.path!.split(".").pop()!.toUpperCase(),
    origin: "page-local",
    status: "ready",
    isExample: true,
    description: `FWD HK · ${n.provenance === "public" ? "Public reference" : "Demo model"} · ${n.label} · ${n.path}`,
    preview: { text: n.content },
    downloadUrl: `data:text/plain;charset=utf-8,${encodeURIComponent(n.content)}`,
  }));

// A small built-in selection for the original Library graph.
const representativeIds = new Set([
  "product-vprime",
  "nb-issue",
  "ps-beneficiary",
  "cl-medical",
  "system-policy",
  "code-beneficiary",
  "test-beneficiary-retry",
  "automation-beneficiary",
  "run-servicing",
  "defect-duplicate",
]);
export const FWD_REPRESENTATIVE_SOURCES = FWD_SOURCES.filter((source) =>
  representativeIds.has(source.id.slice(4)),
).map((source) => {
  const node = nodes.find((node) => `fwd-${node.id}` === source.id)!;
  return {
    ...source,
    name:
      node.kind === "code" || node.kind === "automation"
        ? source.name
        : `${node.label.replace(" · demo run", "")}.${source.type.toLowerCase()}`,
  };
});
export const FWD_REPRESENTATIVE_EDGES = edges.filter(
  (edge) =>
    representativeIds.has(edge.source) && representativeIds.has(edge.target),
);
