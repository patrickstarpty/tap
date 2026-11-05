# Tapper Module Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide answer-execution, conversation-runtime and asset-transaction complexity behind stable application interfaces without changing product behavior.

**Architecture:** Keep the four apps and existing deployment topology. Move ownership to existing domain modules, preserve durable protocols, and make HTTP/worker/UI callers consume results and operations rather than phase mechanics.

**Tech Stack:** Existing Python 3.13/FastAPI/LangGraph/SQLAlchemy and React/TypeScript/TanStack Query; no new dependencies.

**Spec:** [module-depth design](../specs/2026-11-06-module-depth-design.md)

## Global Constraints

- Preserve HTTP/OpenAPI/SSE contracts, checkpoint schema v3, database schema, transaction atomicity, cancellation/lease/retry and authorization behavior.
- No new dependencies, no data reset, no production or remote deployment.
- Preserve Chat→Knowledge public interface and frontend feature isolation.
- Use behavioral tests and dependency checks with negative examples. Run focused tests first; controller owns broad final checks.
- Scope is the four authorized optimizations only. No unrelated cleanup.

### Task 1: Own answer execution in application modules

**Files:** existing `src/tap/interfaces/http/knowledge_service.py`, `entrypoints/tapper_chat_turn.py`, `entrypoints/tapper_draft_batcher.py`, `entrypoints/tapper_generation_worker.py`, `entrypoints/tapper_runtime.py`, `entrypoints/prompt_suggestion_knowledge.py`, `modules/chat/application/ports.py`, `modules/knowledge/api.py`; new execution modules under Chat and Knowledge applications; affected callers/tests and architecture guards. All paths under apps/tap-ai-backend.

**Interfaces:** Keep HTTP operations and worker lifecycle unchanged. Introduce `ConversationExecutionContext` owning frozen input, answer plan and authorization callback. Knowledge owns a resumable conversation-answer implementation and explicitly exports its supported interface. Chat owns `ChatTurnNodes` and draft batching. External callers invoke complete answer execution or the graph runner; graph-internal phases accept the execution context and typed phase values, with JSON conversion at the persisted checkpoint seam. Existing named HTTP response models may remain at delivery conversion only. Do not move an HTTP adapter wholesale into application under a new name.

- [ ] Capture focused baseline: conversation answer steps, retrieval answer phases, chat graph, generation worker and prompt suggestion tests.
- [ ] Add failing architecture/behavior coverage for the new seam: worker no longer imports HTTP runtime; an interrupted prepared step resumes without re-retrieval and still rechecks authorization; typed phase decoder rejects missing phase-required payloads while accepting existing v3 snapshots.
- [ ] Move Knowledge conversation behavior out of HTTP; leave document/source/citation transport handling in HTTP. Inject an application-owned answer executor in composition; update worker and suggestions.
- [ ] Move Chat node and draft orchestration out of entrypoints. Consolidate execution context and stage types; preserve schema encoding and failure classification.
- [ ] Update imports and focused tests; add negative fixtures for dependency direction. Retain all authorization, streaming, cancellation and budget coverage.
- [ ] Run affected tests plus Ruff and relevant type checking, then commit this task only. Record actual RED/GREEN evidence and any retained compatibility adapters.

### Task 2: Encapsulate frontend conversation runtime

**Files:** `apps/tap-ai-frontend/src/features/conversations/api/queries.ts`, new `features/conversations/model/useConversationRuntime.ts` and tests (or neighboring established convention), `widgets/tap/TapperWorkspace.tsx`, affected widget/conversation tests.

**Interfaces:** Export `useConversationRuntime` accepting project/conversation identity and transport-ready send input. It owns detail/events/stream reconciliation, target turn, polling, submit in-flight and idempotency state. Return runtime facts and `send`, `cancel`, and selection/reset operations. Keep widget-specific AssistantTurn and resource/Agent/Skill selection mappings at widget level; never import widgets or another feature into conversations. Route Insights sends through the same runtime ownership while preserving its request contract.

- [ ] Run existing conversation stream and workspace conversation/Insights tests as baseline.
- [ ] Write hook integration tests through real runtime logic and transport stand-ins: replayed drafts survive higher-sequence history, switching conversations ignores late old responses, duplicate sends coalesce/reject and retries reuse intent keys, cancellation and terminal states stop active polling, Insights retains pending key across recoverable failure.
- [ ] Implement runtime hook around existing queries/reducer; move orchestration and send lifecycle from workspace into it. Preserve current rendering and selection behavior.
- [ ] Wire workspace to runtime results/commands; remove superseded state/effects from workspace, not merely forward them through a giant hook parameter list.
- [ ] Run focused Vitest, frontend architecture checks, TypeScript and lint; commit only frontend task files and report RED/GREEN evidence.

### Task 3: Give AI assets ownership of maintenance writes

**Files:** `apps/tap-ai-backend/src/tap/modules/ai/adapters/mysql.py`, new AI maintenance domain/schema/write module(s), `modules/agent_team/{domain/models.py,adapters/mysql.py}`, `modules/skill_hub/{domain/models.py,adapters/mysql.py}`, affected asset authoring and reporting imports/tests.

**Interfaces:** AI owns `MaintenanceEvent`, `SkillMaintenanceEvent`, table definitions and `insert_maintenance_event(session, scope, event)` / `insert_skill_maintenance_event(session, scope, event)` with existing call semantics. Consumers import these from AI, preserving table identity and shared SQLAlchemy metadata. Avoid duplicate/re-export-only production layers where callers can be migrated directly.

- [ ] Run existing asset authoring / agent team / Skill Hub focused tests.
- [ ] Add failing boundary coverage (including negative cases) forbidding AI→reporting-module maintenance persistence dependencies, and transaction regression coverage for event insertion failure rolling back the revision; reuse existing real MySQL integration harness where available.
- [ ] Move shared event values and persistence definitions into AI; update reporting and authoring callers. Preserve existing table/column names, constraints, ownership and locking rules.
- [ ] Run focused unit/contract tests, schema import/metadata tests, Ruff/type checks and isolated transaction tests if available; commit only this task and report evidence.

### Task 4: Review and validate the integrated change

**Files:** `docs/architecture.md`, product roadmap, cross-module ADR, this plan/spec; only integration fixes needed by Tasks 1–3.

- [ ] Review each task against spec and behavioral compatibility; fix actionable findings before accepting it.
- [ ] Update architecture and product roadmap with actual implemented ownership and status, preserving concurrent documentation work during integration.
- [ ] Run `git diff --check`, contract drift checks, `make check`, `make test`, and `make demo-e2e` against isolated owned services. Record external blockers precisely and fix failures introduced by this change.
- [ ] Perform whole-change review; address correctness, architecture and compatibility findings.
- [ ] Integrate verified commits into develop without staging unrelated user edits; report completed scope, validation and remaining blockers accurately.
