---
status: planned
date: 2026-08-23
---

# Phase 1 Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从当前纯文档仓库交付可部署、可恢复、可观测且通过权限与容量验收的 Phase 1 RAG Foundation 和 Athena Knowledge Chat。

**Architecture:** 在单仓库中建立 Vite/React Web 与单 Python package 的 FastAPI 模块化单体，通过独立 entrypoint/image target 运行 API/SSE 和各类 worker。MySQL 是业务事实与 Outbox 的 SoR，Redis 只承担可重建分发和 live fanout，Azure AI Search 是可重建检索投影；浏览器使用 REST snapshot + fetch-based SSE tail，所有 Knowledge 调用都携带服务端构造并在使用时复验的策略上下文。

**Tech Stack:** Python 3.13、uv、FastAPI/ASGI、Pydantic v2、SQLAlchemy 2、Alembic、pytest；Node.js 22 LTS、pnpm 10、Vite、React、TypeScript、Ant Design、Tailwind CSS、TanStack Query、Vitest、Testing Library、Playwright；MySQL、Redis、Azure AI Search、Blob Storage、LiteLLM、Entra ID、Key Vault、AKS、OpenTelemetry。

**Spec:** `docs/proposals/2026-08-23-rfc-003-phase-1-application-structure.md`

## Global Constraints

- 实施前将 RFC-003 从 `in-review` 推进到 `accepted`；未接受前只能执行验证性 spike，不能把其提议写入规范性 architecture/reference 文档。
- 只创建当前任务实际使用的目录、entrypoint 和部署资源；不创建 Phase 1.5、Test IR、Agent、执行网格或空 `packages/`。
- Web 依赖只能沿 `app/pages → widgets → features → shared` 向下；禁止任何 `feature → feature` 导入。
- Backend 领域层不依赖 FastAPI、数据库、Azure SDK、HTTP DTO；跨模块只通过公开应用接口。
- 公共 HTTP DTO 与 SSE event model 由同一 Python/Pydantic 契约代码库生成；生成物提交仓库且 CI 再生后必须无 diff。
- MySQL 保存 Turn、snapshot、append-only event 和 Outbox；Redis 丢失不得丢失业务事实或阻止 snapshot + cursor 恢复。
- 浏览器 DTO 不接受 tenant、ACL、group、classification、任意 filter 或权威 revision；每次 snapshot、tail、citation 和 trace 读取按当前权限重授权。
- REST 错误使用 RFC 9457 `application/problem+json`；SSE framing 与 typed event schema 分开治理。
- `api-sse` 只执行非阻塞异步 I/O；parser、Embedding、本地 rerank 等 CPU/内存密集工作在独立 Pod 中运行。
- 所有队列、连接池、重试、buffer、分页和 fan-out 有界；不得以缓存、拆服务或语言重写替代测量。
- 新增依赖必须由 lockfile 固定；不得提交凭据、tenant 数据、真实访问令牌或本地 `.env`。
- 每项任务在提交前执行其局部测试、`make check`、`git diff --check`；任务 9 执行完整验收。

## Final File Responsibilities

- `Makefile`：开发者和 CI 的唯一顶层命令入口。
- `.python-version`、`pnpm-workspace.yaml`、`package.json`、`pnpm-lock.yaml`、`uv.lock`：固定运行时、workspace 和依赖图。
- `apps/backend/src/tap/contracts/`：唯一人工维护的公共 HTTP/SSE Pydantic 模型。
- `contracts/openapi/api.json`、`contracts/events/chat-stream.schema.json`：确定性生成且提交的跨语言契约。
- `apps/backend/src/tap/modules/`：Access、Projects、Chat、Knowledge 的领域、应用端口和 adapter。
- `apps/backend/src/tap/platform/`：数据库、消息、外部客户端、安全和可观测性技术能力，不承载业务语义。
- `apps/backend/src/tap/entrypoints/`：仅装配依赖并启动 API/SSE 或单一 worker 角色。
- `apps/web/src/features/chat/`：Chat API、stream、store、Markdown 与交互实现。
- `apps/web/src/widgets/athena/`：可嵌入的 `AthenaPanel` 与 `AthenaLauncher` 外壳。
- `deploy/kubernetes/`：实际 Phase 1 角色的 Kubernetes manifests；不保存秘密或未来角色空配置。
- `loadtests/`：REST、SSE、browser 和组合容量场景。

---

### Task 1: Reproducible Workspace and Contract Generation

**Files:**
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `Makefile`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `apps/backend/pyproject.toml`
- Create: `apps/backend/src/tap/contracts/http.py`
- Create: `apps/backend/src/tap/contracts/chat_stream.py`
- Create: `apps/backend/src/tap/interfaces/http/app.py`
- Create: `apps/backend/tests/contract/test_generated_contracts.py`
- Create: `scripts/export_contracts.py`
- Create: `contracts/openapi/api.json`
- Create: `contracts/events/chat-stream.schema.json`
- Create: `uv.lock`
- Create: `pnpm-lock.yaml`
- Modify: `README.md`

**Interfaces:**
- Consumes: RFC-003 contract-generation rules and the Knowledge Chat DTO/event baseline in `docs/reference/2026-08-20-contracts.md`.
- Produces: `tap.interfaces.http.app:create_app()`, public Pydantic event models, deterministic `make contracts`, pinned workspace commands, and committed schema artifacts used by every later task.

- [ ] **Step 1: Accept RFC-003 and mark this plan active**

Change RFC-003 `status: in-review` to `status: accepted` and this plan `status: planned` to `status: active`. Do not change architecture/reference semantics in this step.

- [ ] **Step 2: Write failing deterministic-generation tests**

Create tests that call `scripts/export_contracts.py --check` twice in clean temporary directories, assert byte-identical output, assert stable `operationId: chat_create_turn`, and assert the SSE envelope requires `eventId`, `sequence`, `turnId`, `occurredAt`, `schemaVersion`, and `event`.

Run:

```sh
uv run --project apps/backend pytest apps/backend/tests/contract/test_generated_contracts.py -v
```

Expected: FAIL because the exporter and contract models do not exist.

- [ ] **Step 3: Scaffold the pinned workspace and minimal contracts**

Pin Python `3.13` in `.python-version`, Node `22` in `package.json#engines`, pnpm `10` in `packageManager`, and commit both lockfiles. Implement `create_app()` without startup side effects; define separate HTTP and SSE model graphs; make the exporter sort JSON keys, use two-space indentation, end files with one newline, and omit timestamps.

Root commands must have these meanings:

```make
bootstrap: ## install frozen Python and Node dependencies
check: ## lint, format-check, typecheck, architecture checks
test: ## unit, integration, and contract tests
contracts: ## export OpenAPI/SSE schema and generate TypeScript
```

- [ ] **Step 4: Generate and verify committed artifacts**

Run:

```sh
make bootstrap
make contracts
uv run --project apps/backend pytest apps/backend/tests/contract/test_generated_contracts.py -v
git diff --exit-code -- contracts/
make check
git diff --check
```

Expected: all commands exit `0`; a second generation produces no contract diff.

- [ ] **Step 5: Commit the workspace slice**

```sh
git add .python-version .gitignore .env.example Makefile package.json pnpm-workspace.yaml pnpm-lock.yaml uv.lock apps/backend contracts scripts README.md docs/proposals/2026-08-23-rfc-003-phase-1-application-structure.md docs/plans/2026-08-23-phase-1-application-implementation.md
git commit -m "build: scaffold phase 1 workspace"
```

### Task 2: Persistence, Transactional Outbox, and Worker Dispatch

**Files:**
- Create: `apps/backend/alembic.ini`
- Create: `apps/backend/migrations/env.py`
- Create: `apps/backend/migrations/versions/0001_turn_outbox.py`
- Create: `apps/backend/src/tap/modules/chat/domain/models.py`
- Create: `apps/backend/src/tap/modules/chat/application/ports.py`
- Create: `apps/backend/src/tap/modules/chat/adapters/mysql.py`
- Create: `apps/backend/src/tap/platform/db/session.py`
- Create: `apps/backend/src/tap/platform/messaging/redis_dispatch.py`
- Create: `apps/backend/src/tap/entrypoints/relay_reconciler.py`
- Create: `apps/backend/tests/integration/test_turn_outbox.py`
- Create: `apps/backend/tests/integration/test_relay_recovery.py`

**Interfaces:**
- Consumes: Task 1 Pydantic event envelope and configuration conventions.
- Produces: `TurnRepository.create_with_outbox(command) -> Turn`, `TurnRepository.append_events(turn_id, expected_sequence, events)`, and `Relay.publish_pending(batch_size) -> int` for Task 5.

- [ ] **Step 1: Write failing transaction and recovery tests**

Test that creating a Turn writes Turn and Outbox in one transaction, a forced rollback leaves neither record, duplicate `clientRequestId` in one Chat returns the original `turnId`, event sequence is monotonic, and replaying a claimed Outbox row cannot duplicate a downstream command.

Run:

```sh
uv run --project apps/backend pytest apps/backend/tests/integration/test_turn_outbox.py apps/backend/tests/integration/test_relay_recovery.py -v
```

Expected: FAIL because repositories and migrations do not exist.

- [ ] **Step 2: Implement the minimum schema and ports**

Define immutable IDs and states in the domain module. Create MySQL tables for `chat_turn`, `chat_event`, `turn_snapshot`, and `outbox`; enforce unique `(chat_id, client_request_id)` and `(turn_id, sequence)`. Redis messages contain only command identity and lookup keys, never authoritative Turn state or access tokens.

- [ ] **Step 3: Implement relay lease, retry, and reconciliation**

Claim Outbox rows with a bounded batch, publish idempotently, record attempts and next retry time, and reconcile expired claims. Inject a clock and message publisher so unit tests do not depend on sleeps or a live Redis server.

- [ ] **Step 4: Verify persistence under failure**

Run:

```sh
uv run --project apps/backend alembic upgrade head
uv run --project apps/backend pytest apps/backend/tests/integration/test_turn_outbox.py apps/backend/tests/integration/test_relay_recovery.py -v
make check
git diff --check
```

Expected: migrations apply once and again without error; rollback, duplicate delivery, and worker-restart tests pass.

- [ ] **Step 5: Commit the persistence slice**

```sh
git add apps/backend
git commit -m "feat: add durable turn outbox"
```

### Task 3: Trusted Policy Context and Knowledge Public API

**Files:**
- Create: `apps/backend/src/tap/modules/access/domain/policy.py`
- Create: `apps/backend/src/tap/modules/access/application/authorize.py`
- Create: `apps/backend/src/tap/modules/knowledge/api.py`
- Create: `apps/backend/src/tap/modules/knowledge/domain/models.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/retrieve.py`
- Create: `apps/backend/src/tap/modules/knowledge/ports/search.py`
- Create: `apps/backend/src/tap/modules/knowledge/ports/models.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/azure_ai_search.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/litellm.py`
- Create: `apps/backend/tests/unit/access/test_policy_context.py`
- Create: `apps/backend/tests/contract/test_knowledge_api.py`
- Create: `apps/backend/tests/integration/test_search_acl.py`
- Create: `apps/backend/tests/architecture/test_module_boundaries.py`

**Interfaces:**
- Consumes: authenticated subject facts from the BFF and the Retrieval/Citation contracts.
- Produces: `KnowledgeAPI.search(request: SearchRequest, policy: RetrievalPolicyContext) -> SearchResponse` and `KnowledgeAPI.answer(request: AnswerRequest, policy: RetrievalPolicyContext) -> AnswerResponse` for Chat and future business modules.

- [ ] **Step 1: Write failing authorization and architecture tests**

Cover tenant/project mismatch, group intersection, classification ceiling, environment scope, permission revocation, unauthorized resource anchors, malicious browser filter fields, and imports from Chat into Knowledge adapters. Assert public DTO JSON Schema does not expose `tenantId`, `allowedGroupIds`, `classification`, `filter`, or physical index names.

Run:

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/access apps/backend/tests/contract/test_knowledge_api.py apps/backend/tests/architecture/test_module_boundaries.py -v
```

Expected: FAIL because policy and Knowledge interfaces do not exist.

- [ ] **Step 2: Implement closed domain types and policy construction**

Implement closed enums/unions for source families, resource modes, immutable revision anchors, retrieval profiles, evidence, citations, and abstention reasons. Construct `RetrievalPolicyContext` only from verified Entra subject plus server-side Project Policy; reject policy-unavailable paths rather than widening scope.

- [ ] **Step 3: Implement bounded Search and model adapters**

The Azure adapter must apply mandatory security filters before hybrid/vector search, cap per-index candidates and fan-out, and preserve index/revision provenance. The LiteLLM adapter must enforce deadline, bounded retry, provider request ID capture, and fixed model/profile identifiers; no provider SDK types cross the port.

- [ ] **Step 4: Verify real integration contracts**

Run unit/contract tests with fakes, then run the gated Azure integration suite against a sanitized test tenant:

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/access apps/backend/tests/contract/test_knowledge_api.py apps/backend/tests/architecture/test_module_boundaries.py -v
TAP_RUN_AZURE_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_search_acl.py -v
make contracts
git diff --exit-code -- contracts/
```

Expected: unauthorized hit count is zero and generated contracts remain stable.

- [ ] **Step 5: Commit the Knowledge boundary**

```sh
git add apps/backend contracts
git commit -m "feat: add authorized knowledge api"
```

### Task 4: Recoverable Ingestion and Four Search Indexes

**Files:**
- Create: `apps/backend/migrations/versions/0002_ingestion_ledger.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/ingest.py`
- Create: `apps/backend/src/tap/modules/knowledge/domain/chunks.py`
- Create: `apps/backend/src/tap/modules/knowledge/ports/blob.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/chunkers.py`
- Create: `apps/backend/src/tap/modules/knowledge/adapters/blob_storage.py`
- Create: `apps/backend/src/tap/entrypoints/ingestion_worker.py`
- Create: `apps/backend/src/tap/entrypoints/embedding_worker.py`
- Create: `apps/backend/src/tap/entrypoints/index_writer.py`
- Create: `apps/backend/tests/unit/knowledge/test_chunk_identity.py`
- Create: `apps/backend/tests/integration/test_ingestion_resume_delete.py`
- Create: `apps/backend/tests/integration/test_index_rebuild.py`

**Interfaces:**
- Consumes: Task 2 Outbox/dispatch and Task 3 Search/model ports.
- Produces: stable `logicalChunkId`, immutable `chunkId`, versioned manifests/checkpoints, and four rebuildable indexes `kb-doc-v1`, `kb-code-v1`, `kb-bdd-v1`, `kb-failure-v1`.

- [ ] **Step 1: Write failing identity, resume, deletion, and rebuild tests**

Use sanitized fixtures for Document/Section/Leaf, AST/Symbol, Feature/Scenario/Step, and Incident records. Assert replaying one revision produces identical IDs without duplicates, checkpoint restart resumes after the last acknowledged batch, deletion produces tombstones, ACL tightening removes stale visibility, and rebuild output reconciles with the manifest.

- [ ] **Step 2: Implement typed chunking and provenance**

Follow `docs/architecture/rag/2026-08-21-chunking-and-provenance.md`: structural chunkers first, bounded token fallback second; persist source revision, structural anchor, source/content hashes, parser/chunker/schema/pipeline versions, ACL metadata, and lineage.

- [ ] **Step 3: Implement staged workers and index publication**

Each entrypoint claims one command type, processes a bounded batch, records checkpoint/outcome, and is safe after duplicate delivery or process loss. Index Writer acknowledges a checkpoint only after Azure Search accepts the batch; blue/green publication never mutates the active alias before reconciliation passes.

- [ ] **Step 4: Verify recovery against actual services**

```sh
uv run --project apps/backend pytest apps/backend/tests/unit/knowledge/test_chunk_identity.py apps/backend/tests/integration/test_ingestion_resume_delete.py -v
TAP_RUN_AZURE_INTEGRATION=1 uv run --project apps/backend pytest apps/backend/tests/integration/test_index_rebuild.py -v
make check
git diff --check
```

Expected: duplicate count is zero; delete/ACL changes disappear from authorized search; all four rebuilt indexes reconcile with their manifests.

- [ ] **Step 5: Commit ingestion**

```sh
git add apps/backend
git commit -m "feat: add recoverable knowledge ingestion"
```

### Task 5: Durable Turn Worker, Snapshot, and Fetch-Based SSE

**Files:**
- Create: `apps/backend/src/tap/modules/chat/application/create_turn.py`
- Create: `apps/backend/src/tap/modules/chat/application/run_turn.py`
- Create: `apps/backend/src/tap/modules/chat/application/project_stream.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/turns.py`
- Create: `apps/backend/src/tap/interfaces/http/sse/encode.py`
- Create: `apps/backend/src/tap/entrypoints/api_sse.py`
- Create: `apps/backend/src/tap/entrypoints/turn_worker.py`
- Create: `apps/backend/tests/contract/test_turn_http.py`
- Create: `apps/backend/tests/contract/test_sse_stream.py`
- Create: `apps/backend/tests/integration/test_turn_worker_restart.py`
- Create: `apps/backend/tests/integration/test_stream_backpressure.py`

**Interfaces:**
- Consumes: Task 2 repositories and Task 3 `KnowledgeAPI.answer`.
- Produces: `POST /v1/chats/{chatId}/turns`, `GET /v1/turns/{turnId}`, `GET /v1/turns/{turnId}/events?afterSequence=`, cancel, terminal events, RFC 9457 errors, and bounded replay/reset semantics.

- [ ] **Step 1: Write failing HTTP/SSE state-machine tests**

Cover idempotent create, `202 + turnId`, worker restart, cancel acknowledgement, all terminal states, ordered/duplicate/gap events, snapshot-first recovery, replay overflow, pre-stream `409 application/problem+json`, post-stream `stream.reset_required`, heartbeat, client disconnect, and slow-consumer eviction.

- [ ] **Step 2: Implement Turn orchestration and materialized projection**

Persist Turn/Outbox before returning `202`. Worker reloads subject and policy-decision reference, refreshes authorization before Knowledge access, coalesces provider deltas every `50–100ms` or `32–128` characters, caps output at `10–20 events/s/turn` and `16KiB/event`, and atomically advances events plus snapshot.

- [ ] **Step 3: Implement REST snapshot and SSE tail**

Encode wire `id` as decimal sequence while retaining opaque payload `eventId`. Replay at most `500 events` or `1MiB`; buffer each live connection at `64–128 events` or `256KiB`; disconnect only the slow tail and allow resume. Do not cancel a Turn when the socket closes and do not hold a database connection while waiting.

- [ ] **Step 4: Verify state, recovery, and resource bounds**

```sh
uv run --project apps/backend pytest apps/backend/tests/contract/test_turn_http.py apps/backend/tests/contract/test_sse_stream.py apps/backend/tests/integration/test_turn_worker_restart.py apps/backend/tests/integration/test_stream_backpressure.py -v
make contracts
git diff --exit-code -- contracts/
make check
```

Expected: every Turn reaches exactly one terminal state; replay invokes neither Search nor model; bounded-buffer tests do not grow with an unbounded producer.

- [ ] **Step 5: Commit Turn/SSE**

```sh
git add apps/backend contracts
git commit -m "feat: add recoverable chat streaming"
```

### Task 6: Athena Web Application and Embeddable Panel

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/src/shared/api/generated/`
- Create: `apps/web/src/features/chat/api/turns.ts`
- Create: `apps/web/src/features/chat/stream/fetchSse.ts`
- Create: `apps/web/src/features/chat/state/turnStore.ts`
- Create: `apps/web/src/features/chat/markdown/StreamingMarkdown.tsx`
- Create: `apps/web/src/features/chat/components/ChatWorkspace.tsx`
- Create: `apps/web/src/widgets/athena/AthenaPanel.tsx`
- Create: `apps/web/src/widgets/athena/AthenaLauncher.tsx`
- Create: `apps/web/src/pages/AthenaPage.tsx`
- Create: `apps/web/src/test/AthenaHost.tsx`
- Create: `apps/web/src/features/chat/stream/fetchSse.test.ts`
- Create: `apps/web/src/features/chat/state/turnStore.test.ts`
- Create: `apps/web/src/widgets/athena/AthenaPanel.test.tsx`
- Create: `apps/web/tests/e2e/athena.spec.ts`

**Interfaces:**
- Consumes: Task 1 generated TypeScript client/event union and Task 5 REST/SSE endpoints.
- Produces: standalone Athena page and `AthenaPanelProps { open; contextAnchor; onOpenChange }` embeddable contract validated in a test host.

- [ ] **Step 1: Write failing stream/store/component tests**

Test fragmented SSE framing, comments/heartbeat, multi-line data, unknown events, HTTP Problem Details before streaming, reconnect cursor, duplicate/old/gap sequence, animation-frame batching, context switch isolation, cancel acknowledgement, Markdown XSS, keyboard focus, and host mount/unmount without router or DOM queries.

- [ ] **Step 2: Generate the client and implement fetch SSE**

Generate `shared/api/generated/` only through `make contracts`. Implement an abortable fetch adapter that sends authentication headers, parses the response status before consuming bytes, mirrors the last sequence into explicit `afterSequence`, and treats connection close as transport interruption rather than a Turn terminal state.

- [ ] **Step 3: Implement normalized state and Athena UI**

TanStack Query owns REST server state; a separate store keys hot events by `(turnId, sequence)` and publishes batched render updates. Keep panel, composer, selected citation and focus state local. Sanitize Markdown with an allowlist; lazily load code highlighting, evidence bodies, and Trace details.

- [ ] **Step 4: Verify standalone and embedded behavior**

```sh
pnpm --filter @tap/web test
pnpm --filter @tap/web build
pnpm --filter @tap/web exec playwright test tests/e2e/athena.spec.ts
make contracts
git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/
make check
```

Expected: standalone page and test host pass; context changes cannot display prior-context events or citations; generated files have no manual diff.

- [ ] **Step 5: Commit Athena**

```sh
git add apps/web contracts Makefile package.json pnpm-lock.yaml
git commit -m "feat: add athena knowledge chat"
```

### Task 7: Citation, History, Queue, Feedback, and Revocation Safety

**Files:**
- Create: `apps/backend/src/tap/modules/chat/application/queue.py`
- Create: `apps/backend/src/tap/modules/chat/application/history.py`
- Create: `apps/backend/src/tap/modules/knowledge/application/citations.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/citations.py`
- Create: `apps/backend/src/tap/interfaces/http/routes/feedback.py`
- Create: `apps/backend/tests/security/test_object_authorization.py`
- Create: `apps/backend/tests/security/test_revocation.py`
- Create: `apps/web/src/features/chat/components/CitationDrawer.tsx`
- Create: `apps/web/src/features/chat/components/QueuedMessages.tsx`
- Modify: `apps/web/tests/e2e/athena.spec.ts`

**Interfaces:**
- Consumes: Tasks 3, 5, and 6 policy, Chat, citation, and UI contracts.
- Produces: cursor-paginated history/trace, queue operations, feedback, authorized citation resolution, and fail-closed replay/history behavior after revocation.

- [ ] **Step 1: Write failing security and user-flow tests**

Cover cross-tenant/project Turn IDs, revoked group membership, context-handle reuse after context switch, stale revision, unauthorized Citation/Trace/history/snapshot/tail, queue edit/delete/reorder, cancel-then-correct, resource modes, and structured feedback binding to Turn/Trace/corpus/profile.

- [ ] **Step 2: Implement current-policy reads and queue facts**

Every object read loads the current policy and reauthorizes the referenced immutable revision. If current permission does not cover the Turn authorization snapshot, return Problem Details without old text. Persist queue items as independent facts with IDs/order/`afterTurnId`; never concatenate them into a running query.

- [ ] **Step 3: Implement citation and feedback UI flows**

Load citation bodies and restricted Trace pages on demand. Clear cached evidence on context or policy version change. Keep full diagnostics behind the server-authorized role; never branch on display strings or reveal hidden reasoning/system prompts.

- [ ] **Step 4: Verify adversarial authorization**

```sh
uv run --project apps/backend pytest apps/backend/tests/security -v
pnpm --filter @tap/web test
pnpm --filter @tap/web exec playwright test tests/e2e/athena.spec.ts
make check
```

Expected: unauthorized retrieval/answer/read count is zero, including after revocation and reconnect.

- [ ] **Step 5: Commit complete Chat behavior**

```sh
git add apps/backend apps/web contracts
git commit -m "feat: complete authorized chat flows"
```

### Task 8: Deployment, Observability, and Capacity Gates

**Files:**
- Create: `deploy/kubernetes/base/`
- Create: `deploy/kubernetes/overlays/dev/`
- Create: `deploy/otel/collector.yaml`
- Create: `loadtests/rest/turns.js`
- Create: `loadtests/sse/connections.js`
- Create: `loadtests/browser/athena.ts`
- Create: `loadtests/scenarios/phase1-capacity.md`
- Create: `apps/backend/tests/integration/test_trace_propagation.py`
- Create: `apps/backend/tests/integration/test_health_dependencies.py`
- Modify: `Makefile`
- Modify: `.env.example`

**Interfaces:**
- Consumes: all runtime roles and external-service ports from Tasks 1–7.
- Produces: secret-free manifests, role-specific probes/resources, OTel propagation, and reproducible `make dev`, `make build`, `make loadtest` capacity evidence.

- [ ] **Step 1: Write failing configuration and telemetry tests**

Assert every actual entrypoint has a manifest/image target, no manifest contains literal secrets, `api-sse` is isolated from CPU workers, required configuration fails fast, dependency health is bounded, and `traceparent`, `clientRequestId`, `turnId`, provider request ID, and Outbox sequence propagate across API/worker/Knowledge spans.

- [ ] **Step 2: Implement manifests and health semantics**

Create only Web, `api-sse`, `turn-worker`, `ingestion-worker`, `embedding-worker`, `index-writer`, `relay-reconciler`, LiteLLM, and OTel workloads/configuration. Reference MySQL, Redis, AI Search, Blob, Key Vault and Entra through identity/secret references. Liveness checks process health; readiness checks bounded critical dependencies without creating synchronized retry storms.

- [ ] **Step 3: Implement telemetry and load scenarios**

Instrument API, database, Redis, Search, model and worker boundaries. Load scenarios must exercise `200/500/1000` SSE connections and `20/50/100` active Turns, record receive-to-paint, first visible delta, p95/p99, event-loop lag, queue depth, reconnect duplicate rate, DOM/heap, and slow-consumer count.

- [ ] **Step 4: Run deployment and capacity verification**

```sh
make build
uv run --project apps/backend pytest apps/backend/tests/integration/test_trace_propagation.py apps/backend/tests/integration/test_health_dependencies.py -v
make loadtest TAP_LOAD_PROFILE=ci
kubectl kustomize deploy/kubernetes/overlays/dev >/tmp/tap-phase1-manifests.yaml
rg -n "apiVersion:|kind:|name:" /tmp/tap-phase1-manifests.yaml
make check
git diff --check
```

Expected: manifests render without secrets or absent roles; CI load profile stays within bounded buffers and emits all required measurements. Run peak/failure/soak profiles as the release gate in the target AKS environment.

- [ ] **Step 5: Commit deployment and capacity gates**

```sh
git add deploy loadtests apps/backend Makefile .env.example
git commit -m "ops: add phase 1 deployment gates"
```

### Task 9: Full Acceptance, Documentation Synchronization, and Lifecycle Closure

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture/2026-08-20-overview.md`
- Modify: `docs/architecture/2026-08-21-knowledge-chat-ui.md`
- Modify: `docs/architecture/rag/2026-08-21-foundation.md`
- Modify: `docs/reference/2026-08-20-contracts.md`
- Modify: `docs/plans/2026-08-20-roadmap.md`
- Modify: `docs/proposals/2026-08-23-rfc-003-phase-1-application-structure.md`
- Modify: `docs/plans/2026-08-23-phase-1-application-implementation.md`

**Interfaces:**
- Consumes: verified implementation and evidence from Tasks 1–8.
- Produces: synchronized normative documentation, real developer commands, RFC `implemented`, and plan `completed` only after every Phase 1 acceptance condition passes.

- [ ] **Step 1: Run the complete clean-room command suite**

From a fresh clone or clean isolated worktree with only sanitized configuration, run:

```sh
make bootstrap
make contracts
git diff --exit-code -- contracts/ apps/web/src/shared/api/generated/
make check
make test
make e2e
make build
make loadtest TAP_LOAD_PROFILE=ci
git diff --check
```

Expected: every command exits `0`; no generated drift, skipped required suite, secret, or unbounded-resource failure.

- [ ] **Step 2: Run target-environment release gates**

Run Azure integration, permission/revocation, four-index rebuild/delete/rollback, `200/500/1000` SSE, `20/50/100` active Turn, slow-consumer, worker/Pod restart, Redis-loss recovery, peak, failure, and soak scenarios. Save sanitized result identifiers and approved thresholds in the release record; do not rewrite the RFC’s structural boundaries to fit a failing result.

- [ ] **Step 3: Synchronize normative and operational documentation**

Update the current architecture/reference documents with implemented Problem Details reset semantics, exact commands, actual dependency versions, deployment roles, and measured capacity. Update README/AGENTS only with commands that ran successfully. Preserve canonical terms and distinguish measured SLO/capacity from the original baseline.

- [ ] **Step 4: Close lifecycle states only after evidence review**

Change RFC-003 from `accepted` to `implemented` and this plan from `active` to `completed`. Update `docs/plans/index.md` and `docs/proposals/index.md` statuses. If any required release gate is not met, leave both states unchanged and record the failed gate instead.

- [ ] **Step 5: Verify documentation and commit closure**

```sh
rg --files README.md docs
git diff --check
git diff -- README.md docs/ AGENTS.md
make check
make test
make e2e
make build
```

Expected: lifecycle metadata matches indexes, links and Mermaid diagrams render, commands reflect the repository, and all required suites exit `0`.

```sh
git add README.md AGENTS.md docs apps contracts deploy loadtests scripts Makefile package.json pnpm-workspace.yaml pnpm-lock.yaml uv.lock
git commit -m "docs: complete phase 1 implementation"
```
