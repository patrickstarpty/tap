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

- [x] Capture focused baseline: conversation answer steps, retrieval answer phases, chat graph, generation worker and prompt suggestion tests.
- [x] Add failing architecture/behavior coverage for the new seam: worker no longer imports HTTP runtime; an interrupted prepared step resumes without re-retrieval and still rechecks authorization; typed phase decoder rejects missing phase-required payloads while accepting existing v3 snapshots.
- [x] Move Knowledge conversation behavior out of HTTP; leave document/source/citation transport handling in HTTP. Inject an application-owned answer executor in composition; update worker and suggestions.
- [x] Move Chat node and draft orchestration out of entrypoints. Consolidate execution context and stage types; preserve schema encoding and failure classification.
- [x] Update imports and focused tests; add negative fixtures for dependency direction. Retain all authorization, streaming, cancellation and budget coverage.
- [x] Run affected tests plus Ruff and relevant type checking, then commit this task only. Record actual RED/GREEN evidence and any retained compatibility adapters.

### Task 2: Encapsulate frontend conversation runtime

**Files:** `apps/tap-ai-frontend/src/features/conversations/api/queries.ts`, new `features/conversations/model/useConversationRuntime.ts` and tests (or neighboring established convention), `widgets/tap/TapperWorkspace.tsx`, affected widget/conversation tests.

**Interfaces:** Export `useConversationRuntime` accepting project/conversation identity and transport-ready send input. It owns detail/events/stream reconciliation, target turn, polling, submit in-flight and idempotency state. Return runtime facts and `send`, `cancel`, and selection/reset operations. Keep widget-specific AssistantTurn and resource/Agent/Skill selection mappings at widget level; never import widgets or another feature into conversations. Route Insights sends through the same runtime ownership while preserving its request contract.

- [x] Run existing conversation stream and workspace conversation/Insights tests as baseline.
- [x] Write hook integration tests through real runtime logic and transport stand-ins: replayed drafts survive higher-sequence history, switching conversations ignores late old responses, duplicate sends coalesce/reject and retries reuse intent keys, cancellation and terminal states stop active polling, Insights retains pending key across recoverable failure.
- [x] Implement runtime hook around existing queries/reducer; move orchestration and send lifecycle from workspace into it. Preserve current rendering and selection behavior.
- [x] Wire workspace to runtime results/commands; remove superseded state/effects from workspace, not merely forward them through a giant hook parameter list.
- [x] Run focused Vitest, frontend architecture checks, TypeScript and lint; commit only frontend task files and report RED/GREEN evidence.

### Task 3: Give AI assets ownership of maintenance writes

**Files:** `apps/tap-ai-backend/src/tap/modules/ai/adapters/mysql.py`, new AI maintenance domain/schema/write module(s), `modules/agent_team/{domain/models.py,adapters/mysql.py}`, `modules/skill_hub/{domain/models.py,adapters/mysql.py}`, affected asset authoring and reporting imports/tests.

**Interfaces:** AI owns `MaintenanceEvent`, `SkillMaintenanceEvent`, table definitions and `insert_maintenance_event(session, scope, event)` / `insert_skill_maintenance_event(session, scope, event)` with existing call semantics. Consumers import these from AI, preserving table identity and shared SQLAlchemy metadata. Avoid duplicate/re-export-only production layers where callers can be migrated directly.

- [x] Run existing asset authoring / agent team / Skill Hub focused tests.
- [x] Add failing boundary coverage (including negative cases) forbidding AI→reporting-module maintenance persistence dependencies, and transaction regression coverage for event insertion failure rolling back the revision; reuse existing real MySQL integration harness where available.
- [x] Move shared event values and persistence definitions into AI; update reporting and authoring callers. Preserve existing table/column names, constraints, ownership and locking rules.
- [x] Run focused unit/contract tests, schema import/metadata tests, Ruff/type checks and isolated transaction tests if available; commit only this task and report evidence.

### Task 4: Review and validate the integrated change

**Files:** `docs/architecture.md`, product roadmap, cross-module ADR, this plan/spec; only integration fixes needed by Tasks 1–3.

- [x] Review each task against spec and behavioral compatibility; fix actionable findings before accepting it.
- [x] Update architecture and product roadmap with actual implemented ownership and status, preserving concurrent documentation work during integration.
- [x] Run `git diff --check`, contract drift checks, `make check`, `make test`, and `make demo-e2e` against isolated owned services. Record external blockers precisely and fix failures introduced by this change.
- [x] Perform whole-change review; address correctness, architecture and compatibility findings.
- [ ] Integrate verified commits into develop without staging unrelated user edits; report completed scope, validation and remaining blockers accurately.

## 验证记录（2026-11-06）

- `make check` 通过：代码规范、类型、应用边界、生成契约、构建与确定性质量门禁。
- `make test` 通过：TAP AI 后端 5411 passed / 492 skipped，TAP AI 前端 1078 passed，TAP 后端 147 passed / 35 skipped，TAP Web 240 passed；合计 6876 passed / 527 skipped。10 条 Alembic 警告与原版基线一致。
- 独立 MySQL/Redis 验证：checkpoint 模型失败恢复 1 项，发布授权复检与取消 5 项，资产修订/反馈/owner 与维护记录原子性 4 项通过；两张维护事件表及三个索引的 MySQL DDL 与迁移前一致。
- `make demo-e2e` 通过：业务流程 13 项、应用重启 1 项、整套服务重启 1 项、持久化校验 32 项通过；隔离资源清理完成。该门禁使用确定性模型，不代表真实模型效果验收。
- 三个实施任务和最终 71 文件评审通过。评审发现的同会话重复选择、显式重置幂等键问题均有先失败后通过的行为测试；恢复测试的历史时钟漂移在原版亦可复现，修正仅限测试时钟。
