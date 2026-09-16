import "@testing-library/jest-dom/vitest";
import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { clearTestQueryClients } from "./renderApp";

class TestResizeObserver implements ResizeObserver {
  disconnect(): void {}

  observe(): void {}

  unobserve(): void {}
}

Object.defineProperty(window, "ResizeObserver", {
  configurable: true,
  value: TestResizeObserver,
});

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => true,
  }),
});

Object.defineProperty(window, "scrollTo", {
  configurable: true,
  value: () => undefined,
});

const browserGetComputedStyle = window.getComputedStyle.bind(window);
Object.defineProperty(window, "getComputedStyle", {
  configurable: true,
  value: (element: Element) => browserGetComputedStyle(element),
});

let consoleMessages: string[] = [];
let restoreConsole = () => undefined;

beforeEach(() => {
  consoleMessages = [];
  const record = (...arguments_: unknown[]) => {
    consoleMessages.push(arguments_.map(String).join(" "));
  };
  const errorSpy = vi.spyOn(console, "error").mockImplementation(record);
  const warningSpy = vi.spyOn(console, "warn").mockImplementation(record);
  restoreConsole = () => {
    errorSpy.mockRestore();
    warningSpy.mockRestore();
  };
});

afterEach(() => {
  cleanup();
  clearTestQueryClients();
  notifyManager.setScheduler(defaultScheduler);
  vi.useRealTimers();
  restoreConsole();
  if (consoleMessages.length > 0) {
    throw new Error(
      `Unexpected console output:\n${consoleMessages.join("\n")}`,
    );
  }
});
