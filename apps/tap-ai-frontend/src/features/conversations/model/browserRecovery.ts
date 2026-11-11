import type { ConversationCreateRequest } from "../api/client";
import {
  parseLcaContext,
  sameLcaContext,
  type LcaGatewayContext,
} from "../../../shared/api/browserContext";
export const browserRecoveryKey = "tap:browser-intent:v1";
export interface FrozenBrowserIntent {
  conversationId: string;
  input: ConversationCreateRequest;
  key: string;
  attempts: number;
}
type RecordState = {
  version: 1;
  context: LcaGatewayContext;
  pending: FrozenBrowserIntent | null;
  conversationId: string | null;
};
const id = /^[A-Za-z0-9_-]{1,80}$/u;
const failure = () =>
  new Error(
    "Browser recovery is unavailable. Resolve the saved request before starting another run.",
  );
/** Session-only user-request copy; excludes transport credentials, not a secret detector. */
export class BrowserRecovery {
  constructor(
    private readonly storage: Pick<
      Storage,
      "getItem" | "setItem" | "removeItem"
    >,
    private readonly context: LcaGatewayContext,
  ) {}
  private read(): RecordState | null {
    try {
      const raw = this.storage.getItem(browserRecoveryKey);
      if (raw === null) return null;
      if (raw.length > 65_536) throw failure();
      const value = JSON.parse(raw) as RecordState;
      if (
        value.version !== 1 ||
        !sameLcaContext(parseLcaContext(value.context), this.context)
      )
        throw new Error(
          "Saved browser request belongs to a different authorized context. The unconfirmed request may already be running. Review task history to identify it before starting a replacement.",
        );
      if (value.pending !== null) {
        if (
          !value.pending ||
          !id.test(value.pending.key) ||
          !id.test(value.pending.conversationId) ||
          !Number.isSafeInteger(value.pending.attempts) ||
          value.pending.attempts < 0 ||
          value.pending.attempts > 1_000_000
        )
          throw failure();
        this.validate(value.pending.input);
      }
      if (value.conversationId !== null && !id.test(value.conversationId))
        throw failure();
      return value;
    } catch (error) {
      if (error instanceof Error && /authorized context/.test(error.message))
        throw error;
      throw failure();
    }
  }
  private validate(input: ConversationCreateRequest) {
    if (
      !input ||
      !input.browser ||
      typeof input.message !== "string" ||
      typeof input.modelAlias !== "string" ||
      Object.keys(input).some(
        (key) =>
          ![
            "message",
            "modelAlias",
            "sourceRevisionIds",
            "documentRevisionIds",
            "skillRevisionIds",
            "agentRevisionId",
            "requestedAgentIds",
            "insightsReportRefs",
            "workflowTaskId",
            "browser",
          ].includes(key),
      ) ||
      Object.keys(input.browser).some(
        (key) =>
          ![
            "startUrl",
            "expectedResult",
            "inputNotes",
            "acceptanceContract",
            "sourceTaskId",
          ].includes(key),
      ) ||
      /"(?:authorization|headers|password|token|script|credentials)"\s*:/iu.test(
        JSON.stringify(input),
      ) ||
      JSON.stringify(input).length > 48_000
    )
      throw failure();
  }
  /** A known reference is safe to inspect after restart; it is never execution authority. */
  reference(): { conversationId: string | null; unconfirmed: boolean } | null {
    try {
      const raw = this.storage.getItem(browserRecoveryKey);
      if (raw === null || raw.length > 65_536) return null;
      const state = JSON.parse(raw) as RecordState;
      const saved = parseLcaContext(state.context);
      if (
        state.version !== 1 ||
        saved.project_id !== this.context.project_id ||
        saved.actor_id !== this.context.actor_id ||
        saved.identity_mode !== this.context.identity_mode ||
        (state.conversationId !== null && !id.test(state.conversationId))
      )
        return null;
      return {
        conversationId: state.conversationId,
        unconfirmed: state.pending !== null,
      };
    } catch {
      return null;
    }
  }
  adoptAccepted(conversationId: string) {
    const reference = this.reference();
    if (
      !reference ||
      reference.unconfirmed ||
      reference.conversationId !== conversationId
    )
      throw new Error(
        "An unconfirmed browser request cannot be released after a gateway change.",
      );
    try {
      this.storage.setItem(
        browserRecoveryKey,
        JSON.stringify({
          version: 1,
          context: this.context,
          pending: null,
          conversationId,
        }),
      );
    } catch {
      throw failure();
    }
  }
  pending() {
    return this.read()?.pending ?? null;
  }
  conversation() {
    return this.read()?.conversationId ?? null;
  }
  prepare(
    conversationId: string,
    input: ConversationCreateRequest,
  ): FrozenBrowserIntent {
    this.validate(input);
    if (!id.test(conversationId)) throw failure();
    const old = this.read();
    if (old?.pending) {
      if (
        old.pending.conversationId !== conversationId ||
        JSON.stringify(old.pending.input) !== JSON.stringify(input)
      )
        throw new Error(
          "An unconfirmed browser request must be recovered before starting another run.",
        );
      return old.pending;
    }
    const pending = {
      conversationId,
      input: JSON.parse(JSON.stringify(input)) as ConversationCreateRequest,
      key: crypto.randomUUID(),
      attempts: 0,
    };
    const state: RecordState = {
      version: 1,
      context: this.context,
      pending,
      conversationId: old?.conversationId ?? null,
    };
    try {
      this.storage.setItem(browserRecoveryKey, JSON.stringify(state));
      if (this.storage.getItem(browserRecoveryKey) !== JSON.stringify(state))
        throw failure();
    } catch {
      throw failure();
    }
    return pending;
  }
  markAttempt(key: string) {
    const state = this.read();
    if (!state?.pending || state.pending.key !== key) throw failure();
    try {
      this.storage.setItem(
        browserRecoveryKey,
        JSON.stringify({
          ...state,
          pending: { ...state.pending, attempts: state.pending.attempts + 1 },
        }),
      );
    } catch {
      throw failure();
    }
  }
  rejectUnadmitted(key: string, status: number): boolean {
    const state = this.read();
    // Only the first response can establish that no earlier admission exists.
    // A later 403/404 says nothing about a previous lost acknowledgement.
    if (
      !state?.pending ||
      state.pending.key !== key ||
      state.pending.attempts !== 1 ||
      ![400, 401, 403, 404, 422].includes(status)
    )
      return false;
    try {
      this.storage.setItem(
        browserRecoveryKey,
        JSON.stringify({ ...state, pending: null }),
      );
    } catch {
      throw failure();
    }
    return true;
  }
  accept(key: string, conversationId: string) {
    const state = this.read();
    if (
      !state?.pending ||
      state.pending.key !== key ||
      !id.test(conversationId)
    )
      throw failure();
    try {
      this.storage.setItem(
        browserRecoveryKey,
        JSON.stringify({ ...state, pending: null, conversationId }),
      );
    } catch {
      throw failure();
    }
  }
}
