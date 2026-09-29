export type PrototypeFault =
  | "send-failed"
  | "stop-failed"
  | "stream-interrupted"
  | "no-models"
  | "history-load-failed"
  | "conversation-delete-failed"
  | "sources-load-failed"
  | "library-load-failed"
  | "upload-failed"
  | "graph-load-failed"
  | "citation-verification-failed"
  | "source-version-changed";

declare global {
  interface Window {
    __TAP_PROTOTYPE_FAULTS__?: readonly string[];
  }
}

const KNOWN_FAULTS: readonly PrototypeFault[] = [
  "send-failed",
  "stop-failed",
  "stream-interrupted",
  "no-models",
  "history-load-failed",
  "conversation-delete-failed",
  "sources-load-failed",
  "library-load-failed",
  "upload-failed",
  "graph-load-failed",
  "citation-verification-failed",
  "source-version-changed",
];

function isPrototypeFault(value: string): value is PrototypeFault {
  return (KNOWN_FAULTS as readonly string[]).includes(value);
}

let activeFaults: Set<PrototypeFault> | undefined;

function getActiveFaults(): Set<PrototypeFault> {
  if (!activeFaults) {
    const injected = typeof window !== "undefined" ? window.__TAP_PROTOTYPE_FAULTS__ : undefined;
    activeFaults = new Set((injected ?? []).filter(isPrototypeFault));
  }
  return activeFaults;
}

export function isPrototypeFaultActive(fault: PrototypeFault): boolean {
  return getActiveFaults().has(fault);
}

export function clearPrototypeFault(fault: PrototypeFault): void {
  getActiveFaults().delete(fault);
}

export function takePrototypeFault(fault: PrototypeFault): boolean {
  const faults = getActiveFaults();
  const active = faults.has(fault);
  if (active) {
    faults.delete(fault);
  }
  return active;
}

export function setPrototypeFaults(faults: readonly PrototypeFault[]): void {
  activeFaults = new Set(faults);
}
