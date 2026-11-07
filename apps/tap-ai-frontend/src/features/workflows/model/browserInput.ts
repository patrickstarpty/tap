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
}) {
  return {
    startUrl: input.startUrl.trim() || startUrlFromGoal(input.goal),
    ...(input.notes.trim() ? { inputNotes: input.notes.trim() } : {}),
    ...(input.expectedResult.trim()
      ? { expectedResult: input.expectedResult.trim() }
      : {}),
    ...(input.sourceTaskId ? { sourceTaskId: input.sourceTaskId } : {}),
  };
}
