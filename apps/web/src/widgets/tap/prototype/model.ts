export type Locale = "en" | "zh";

export type ProductModule =
  "athena" | "agents" | "skills" | "library" | "test-management" | "low-code";

export type AthenaSurface = "chat" | "agents" | "skills" | "library";

export type AssistantIntent = "answer" | "test-plan" | "automation";

export type CatalogKind = "agent" | "skill";

export type CatalogOrigin = "built-in" | "custom";

export interface AssistantTurn {
  id: string;
  intent: AssistantIntent;
  prompt: string;
}

export interface Conversation {
  id: string;
  title: string;
  turns: readonly AssistantTurn[];
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
  type: string;
  status: "ready" | "processing" | "failed";
  description: string;
}

export interface CreateConversationOptions {
  title?: string;
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

const AUTOMATION_CUES = [
  "自动化",
  "脚本",
  "automation",
  "automate",
  "script",
  "playwright",
] as const;

const TEST_PLAN_CUES = [
  "测试用例",
  "测试计划",
  "测试场景",
  "bdd",
  "test case",
  "test plan",
  "test scenario",
  "tests",
] as const;

function containsAny(value: string, cues: readonly string[]): boolean {
  return cues.some((cue) => value.includes(cue));
}

export function detectIntent(prompt: string): AssistantIntent {
  const normalized = prompt.toLowerCase();
  if (!containsAny(normalized, CREATION_CUES)) return "answer";
  if (containsAny(normalized, AUTOMATION_CUES)) return "automation";
  if (containsAny(normalized, TEST_PLAN_CUES)) return "test-plan";
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
