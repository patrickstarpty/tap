import type { LcaDefinition, LcaStep } from "./client";

export const pageLifecycle = (step: LcaStep) =>
  ["open_page", "switch_page", "close_page"].includes(step.operation);

/** Derive page state in canonical order without repairing an invalid draft. */
export function pageContextBefore(definition: LcaDefinition, stepId?: string) {
  const open = new Set(["main"]);
  const used = new Set(["main"]);
  let active = "main";
  for (const step of definition.steps) {
    if (step.id === stepId) break;
    const destination = step.arguments.page_id;
    if (typeof destination !== "string") continue;
    if (["open_page", "click_popup"].includes(step.operation)) {
      open.add(destination);
      used.add(destination);
    } else if (step.operation === "switch_page") active = destination;
    else if (step.operation === "close_page") open.delete(destination);
  }
  return { open, used, active };
}

export function pageContextError(definition: LcaDefinition, step: LcaStep) {
  const context = pageContextBefore(definition, step.id);
  const id = step.arguments.page_id;
  if (pageLifecycle(step) && step.page_id != null)
    return "Page lifecycle actions use their destination page ID, not a source page binding.";
  if (!pageLifecycle(step) && !context.open.has(step.page_id ?? context.active))
    return `Page ${step.page_id ?? context.active} is not open before this action.`;
  if (
    ["open_page", "click_popup", "switch_page", "close_page"].includes(
      step.operation,
    )
  ) {
    if (typeof id !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(id))
      return "Enter a static page ID beginning with a lowercase letter (up to 64 lowercase letters, digits or underscores).";
    if (["open_page", "click_popup"].includes(step.operation))
      return context.used.has(id)
        ? `Page ID ${id} has already been used. Choose a new ID.`
        : null;
    if (!context.open.has(id))
      return `Page ${id} is not open before this action.`;
    if (step.operation === "close_page" && id === context.active)
      return "Switch to another page before closing the active page.";
  }
  return null;
}
