import type { KeyboardEvent } from "react";

/** Tab reached by a tablist navigation key, or null for any other key. */
export function tabForKey<T>(
  key: string,
  tabs: readonly T[],
  current: T,
): T | null {
  const index = Math.max(tabs.indexOf(current), 0);
  switch (key) {
    case "ArrowRight":
      return tabs[(index + 1) % tabs.length];
    case "ArrowLeft":
      return tabs[(index - 1 + tabs.length) % tabs.length];
    case "Home":
      return tabs[0];
    case "End":
      return tabs[tabs.length - 1];
    default:
      return null;
  }
}

/** WAI-ARIA tablist keys with automatic activation: select the next tab and
 * move focus to it (pair with a roving tabindex on the tabs). */
export function handleTablistKey<T>(
  event: KeyboardEvent,
  tabs: readonly T[],
  current: T,
  select: (tab: T) => void,
  tabId: (tab: T) => string,
) {
  const next = tabForKey(event.key, tabs, current);
  if (next === null) return;
  event.preventDefault();
  select(next);
  document.getElementById(tabId(next))?.focus();
}
