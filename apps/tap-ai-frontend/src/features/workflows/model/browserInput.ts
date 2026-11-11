/** Use a URL the user already wrote in the goal as the editable start URL. */
export function startUrlFromGoal(goal: string): string {
  const candidate = goal
    .match(/https?:\/\/[^\s<>"'()]+/iu)?.[0]
    ?.replace(/[.,;!?]+$/u, "");
  if (!candidate) return "";
  try {
    const url = new URL(candidate);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      !url.hostname ||
      url.username ||
      url.password ||
      url.hash
    )
      return "";
    return candidate;
  } catch {
    return "";
  }
}

export function browserSendInput(input: {
  goal: string;
  startUrl: string;
  notes: string;
  expectedResult: string;
  sourceTaskId: string | null;
  acceptanceContract?: BrowserAcceptanceContract;
}) {
  return {
    ...(input.acceptanceContract
      ? {
          acceptanceContract: acceptanceContractFromDraft(
            input.acceptanceContract,
          ),
        }
      : {}),
    startUrl: input.startUrl.trim() || startUrlFromGoal(input.goal),
    ...(input.notes.trim() ? { inputNotes: input.notes.trim() } : {}),
    ...(input.expectedResult.trim()
      ? { expectedResult: input.expectedResult.trim() }
      : {}),
    ...(input.sourceTaskId ? { sourceTaskId: input.sourceTaskId } : {}),
  };
}

export const BROWSER_READBACK_ATTRIBUTES = [
  "text",
  "value",
  "checked",
  "aria-pressed",
  "aria-valuenow",
  "data-active",
  "selected_label",
] as const;

export interface BrowserAcceptanceField {
  id: string;
  value: string;
  readbackValue: string;
  attribute: (typeof BROWSER_READBACK_ATTRIBUTES)[number];
  targetTexts: string[];
  scopeText?: string | null;
}

export interface BrowserAcceptanceContract {
  version: 1;
  reviewed: boolean;
  fields: BrowserAcceptanceField[];
}

export interface BrowserAcceptanceDraft {
  reviewed: boolean;
  fields: BrowserAcceptanceField[];
}

export function browserAcceptanceValid(draft: BrowserAcceptanceDraft): boolean {
  return (
    draft.fields.length > 0 &&
    draft.fields.length <= 20 &&
    new Set(draft.fields.map((field) => field.id)).size ===
      draft.fields.length &&
    draft.fields.every(
      (field) =>
        /^[A-Za-z0-9_-]{1,80}$/.test(field.id) &&
        ![
          "final_url",
          "provenance",
          "source_url",
          "observed_at",
          "observation_sequence",
          "source_frame_id",
          "source_document_id",
        ].includes(field.id) &&
        field.value.length <= 4000 &&
        field.readbackValue.length <= 4000 &&
        BROWSER_READBACK_ATTRIBUTES.includes(field.attribute) &&
        field.targetTexts.length > 0 &&
        field.targetTexts.length <= 6 &&
        (field.scopeText == null ||
          (field.scopeText.trim().length > 0 &&
            field.scopeText.length <= 200)) &&
        field.targetTexts.every(
          (text) => text.trim().length > 0 && text.length <= 200,
        ),
    )
  );
}

export function browserAcceptanceReady(draft: BrowserAcceptanceDraft): boolean {
  return (
    draft.fields.length === 0 ||
    (draft.reviewed && browserAcceptanceValid(draft))
  );
}

/** Detach the accepted snapshot from the editable composer and subsequent runs. */
export function acceptanceContractFromDraft(
  draft: BrowserAcceptanceDraft,
): BrowserAcceptanceContract | undefined {
  if (!draft.fields.length) return undefined;
  if (!browserAcceptanceReady(draft))
    throw new Error("Review every required input before sending.");
  return {
    version: 1,
    reviewed: true,
    fields: draft.fields.map((field) => ({
      ...field,
      targetTexts: [...field.targetTexts],
    })),
  };
}

export function acceptanceDraftFromContract(
  contract?: BrowserAcceptanceContract | null,
): BrowserAcceptanceDraft {
  return {
    reviewed: contract?.reviewed === true,
    fields:
      contract?.fields.map((field) => ({
        ...field,
        targetTexts: [...field.targetTexts],
      })) ?? [],
  };
}
