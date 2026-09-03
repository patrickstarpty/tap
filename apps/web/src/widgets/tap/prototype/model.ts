export type Locale = "en" | "zh";

export type CodexModelId =
  "gpt-5.6-sol" | "gpt-5.6-terra" | "gpt-5.6-luna" | "gpt-5.5" | "gpt-5.4";

export const CODEX_MODELS: readonly {
  id: CodexModelId;
  label: string;
}[] = [
  { id: "gpt-5.6-sol", label: "GPT-5.6 Sol" },
  { id: "gpt-5.6-terra", label: "GPT-5.6 Terra" },
  { id: "gpt-5.6-luna", label: "GPT-5.6 Luna" },
  { id: "gpt-5.5", label: "GPT-5.5" },
  { id: "gpt-5.4", label: "GPT-5.4" },
] as const;

export const DEFAULT_CODEX_MODEL_ID: CodexModelId = "gpt-5.6-sol";

export function isCodexModelId(value: unknown): value is CodexModelId {
  return CODEX_MODELS.some((model) => model.id === value);
}

export type ProductModule =
  "athena" | "agents" | "skills" | "library" | "test-management" | "low-code";

export type AthenaSurface = "chat" | "agents" | "skills" | "library";

export type AssistantIntent = "answer" | "test-plan" | "automation";

export type InferredAutomationType = "web" | "mobile";

export type CatalogKind = "agent" | "skill";

export type CatalogOrigin = "built-in" | "custom";

export type LibrarySourceOrigin = "knowledge-base" | "page-local";

export interface AssistantSourceReference {
  id: string;
  name: string;
  origin: LibrarySourceOrigin;
}

export type AutomationAction =
  "Navigate" | "Click" | "Fill" | "Assert" | "Wait";

export interface AutomationStepSnapshot {
  action: AutomationAction;
  id: string;
  target: string;
  value: string;
}

export interface AssistantTurn {
  id: string;
  intent: AssistantIntent;
  locale: Locale;
  modelId: CodexModelId;
  prompt: string;
  sourceReferences: readonly AssistantSourceReference[];
  automationSteps?: readonly AutomationStepSnapshot[];
  automationWorkflow?: {
    stage:
      | "ask-test-plan"
      | "review-test-plan"
      | "choose-automation-type"
      | "ready-linked"
      | "ready-unlinked";
    testPlanId: string | null;
    automationId: string | null;
    automationType: InferredAutomationType | null;
  };
}

export interface Conversation {
  id: string;
  title: string;
  turns: readonly AssistantTurn[];
  modelId: CodexModelId;
  selectedSourceIds: readonly string[];
  selectedAgentIds: readonly string[];
  selectedSkillIds: readonly string[];
}

export interface CatalogItem {
  id: string;
  kind: CatalogKind;
  origin: CatalogOrigin;
  name: string;
  description: string;
  instructions: string;
}

export interface LibrarySource {
  id: string;
  name: string;
  origin: LibrarySourceOrigin;
  type: string;
  status: "ready" | "processing" | "failed";
  description: string;
}

export interface CreateConversationOptions {
  title?: string;
  modelId?: CodexModelId;
  selectedSourceIds?: readonly string[];
  selectedAgentIds?: readonly string[];
  selectedSkillIds?: readonly string[];
}

const CREATION_CUES = [
  "生成",
  "创建",
  "编写",
  "设计",
  "构建",
  "产出",
  "generate",
  "create",
  "draft",
  "write",
  "design",
  "build",
  "produce",
  "automate",
] as const;

const REQUEST_CUES = [
  "请",
  "帮我",
  "给我",
  "我需要",
  "我要",
  "需要一份",
  "i need",
  "i want",
  "please",
  "can you",
  "could you",
  "would you",
  "help me",
  "give me",
  "prepare",
] as const;

const QUESTION_CUES = [
  "什么",
  "哪些",
  "为什么",
  "如何",
  "怎么",
  "是否",
  "解释",
  "说明",
  "what",
  "which",
  "why",
  "how",
  "explain",
  "describe",
  "compare",
  "review",
] as const;

const AUTOMATION_CUES = [
  "自动化",
  "脚本",
  "automation",
  "automate",
  "script",
  "playwright",
] as const;

const WEB_AUTOMATION_CUES = [
  "web",
  "browser",
  "playwright",
  "website",
  "网页",
  "网站",
  "浏览器",
] as const;

const MOBILE_AUTOMATION_CUES = [
  "mobile",
  "ios",
  "android",
  "device",
  "native app",
  "移动端",
  "手机",
  "安卓",
] as const;

const TEST_PLAN_CUES = [
  "测试用例",
  "测试计划",
  "测试场景",
  "bdd",
  "test case",
  "test cases",
  "test plan",
  "test plans",
  "test scenario",
  "test scenarios",
  "tests",
] as const;

const DIRECT_AUTOMATION_ARTIFACT_CUES = [
  "自动化脚本",
  "playwright 脚本",
  "automation script",
  "automated test",
  "playwright script",
] as const;

const DIRECT_TEST_PLAN_ARTIFACT_CUES = [
  "测试用例",
  "测试计划",
  "测试场景",
  "bdd",
  "test case",
  "test cases",
  "test plan",
  "test plans",
  "test scenario",
  "test scenarios",
] as const;

function cueIndex(value: string, cue: string): number {
  if (!/^[a-z ]+$/.test(cue)) return value.indexOf(cue);
  const escaped = cue.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`\\b${escaped}\\b`).exec(value)?.index ?? -1;
}

function firstCueIndex(value: string, cues: readonly string[]): number {
  return cues.reduce((firstIndex, cue) => {
    const index = cueIndex(value, cue);
    if (index < 0) return firstIndex;
    return firstIndex < 0 ? index : Math.min(firstIndex, index);
  }, -1);
}

function containsAny(value: string, cues: readonly string[]): boolean {
  return firstCueIndex(value, cues) >= 0;
}

export function detectAutomationType(
  prompt: string,
): InferredAutomationType | null {
  const normalized = prompt.trim().toLowerCase();
  const indicatesWeb = containsAny(normalized, WEB_AUTOMATION_CUES);
  const indicatesMobile = containsAny(normalized, MOBILE_AUTOMATION_CUES);
  if (indicatesWeb === indicatesMobile) return null;
  return indicatesWeb ? "web" : "mobile";
}

export function detectIntent(prompt: string): AssistantIntent {
  const normalized = prompt.trim().toLowerCase();
  const automationIntent = containsAny(normalized, AUTOMATION_CUES);
  const testPlanIntent = containsAny(normalized, TEST_PLAN_CUES);

  if (!automationIntent && !testPlanIntent) return "answer";

  const creationCueIndex = firstCueIndex(normalized, CREATION_CUES);
  const questionCueIndex = firstCueIndex(normalized, QUESTION_CUES);
  const explicitCreation = creationCueIndex >= 0;
  const requestedOutput = containsAny(normalized, REQUEST_CUES);
  const asksForExplanation = questionCueIndex >= 0;

  if (
    asksForExplanation &&
    (/[?？]/.test(normalized) ||
      !explicitCreation ||
      questionCueIndex <= creationCueIndex)
  ) {
    return "answer";
  }

  const directArtifact =
    containsAny(normalized, DIRECT_AUTOMATION_ARTIFACT_CUES) ||
    containsAny(normalized, DIRECT_TEST_PLAN_ARTIFACT_CUES);

  if (!explicitCreation && !requestedOutput && !directArtifact) {
    return "answer";
  }

  if (automationIntent) return "automation";
  if (testPlanIntent) return "test-plan";
  return "answer";
}

export function createConversation(
  id: string,
  options: CreateConversationOptions = {},
): Conversation {
  return {
    id,
    title: options.title ?? "New chat",
    turns: [],
    modelId: options.modelId ?? DEFAULT_CODEX_MODEL_ID,
    selectedSourceIds: [...(options.selectedSourceIds ?? [])],
    selectedAgentIds: [...(options.selectedAgentIds ?? [])],
    selectedSkillIds: [...(options.selectedSkillIds ?? [])],
  };
}

export function appendTurn(
  conversation: Conversation,
  turn: AssistantTurn,
): Conversation {
  return {
    ...conversation,
    title:
      conversation.turns.length === 0 && conversation.title === "New chat"
        ? turn.prompt
        : conversation.title,
    turns: [...conversation.turns, turn],
  };
}
