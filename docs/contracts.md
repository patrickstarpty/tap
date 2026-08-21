# TAP 核心契约

本页定义架构级契约，而不是最终 API。字段在实现前可以扩展，但不能破坏不可变性、幂等、租户隔离和可追溯性。框架代码、BrowserStack capability 和 Agent Runtime 对象都不能成为领域契约。

## 1. Test IR 与稳定身份

Test IR 是 Git 中版本化的核心资产。稳定身份与文件路径、名称和执行框架解耦：

```yaml
apiVersion: tap.dev/v1alpha1
kind: TestCase
metadata:
  id: test_checkout_happy_path
  projectId: commerce-web
  aliases: []
spec:
  intent: "已登录用户完成信用卡结账"
  steps:
    - id: submit_payment
      action: click
      target: { locatorRef: checkout.submit }
  assertions:
    - type: url_matches
      value: /orders/*
```

约束：

- `metadata.id` 创建后不可复用；重命名通过 alias/migration 表达。
- Test IR revision 由 Git commit + content hash 标识，不使用可变 branch 名。
- action、target、assertion、fixture 和 secret 必须是版本化 typed vocabulary。
- 自定义能力通过显式 extension namespace 表达；禁止把任意 Shell 当通用 action。
- MySQL 保存 Test catalog/projection 与 revision 映射，内容版本以 Git 为准。

## 2. RunSpec

Run 创建后冻结。任何重跑都创建新的 Attempt；改变工作流、策略或 revision 必须创建新 Run。

```yaml
apiVersion: tap.dev/v1alpha1
kind: Run
metadata:
  tenantId: tenant_123
  projectId: project_456
  idempotencyKey: github:delivery:abc123
spec:
  trigger:
    type: github.pull_request
    actor: github:user:octocat
  source:
    repository: github:owner/repo
    revision: 0123456789abcdef
    baseRevision: fedcba9876543210
  testAssets:
    - id: test_checkout_happy_path
      revision: 0123456789abcdef
      contentHash: sha256:...
  workflowRef:
    id: pr-quality
    version: 7
  policyRef:
    id: default-engineering
    version: 4
  budget:
    deadlineSeconds: 3600
    maxAgentTokens: 200000
    maxProviderMinutes: 120
  requestedCapabilities:
    - agent.analysis
    - test.web.e2e
```

## 3. Task 与 Attempt

- Task 是归一化逻辑工作项，可以来自 Workflow DAG 节点，也可以来自 Agentic Loop 某轮规划的 action。
- DAG Task 具有稳定 node key；Loop Task 必须记录 `plan_iteration`、`action_id` 与产生它的 causation event。
- Loop 在产生任何工具或外部副作用前，必须先持久化 Task/Attempt；动态规划不能绕开运行状态机。
- Attempt 是 Task 的一次物理执行，具有单调递增序号。
- 重试不能复用外部 Provider attempt ID 或 Agent session ID。
- Task 成功规则由 Workflow 定义；Attempt 终态不可修改。

下面是 **Attempt 状态机**；Task 的汇总结论由 Workflow/Plan 根据一个或多个 Attempt 计算。

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> leased
    leased --> running
    running --> awaiting_approval
    awaiting_approval --> running: approved
    awaiting_approval --> failed: rejected
    awaiting_approval --> canceled: canceled
    awaiting_approval --> timed_out: expired
    running --> succeeded
    running --> failed
    running --> canceled
    leased --> timed_out
    leased --> canceled
    running --> timed_out
    queued --> canceled
    failed --> [*]
    succeeded --> [*]
    canceled --> [*]
    timed_out --> [*]
```

未知或供应商特有状态映射为 `running` 加诊断属性，不能乐观映射为 `succeeded`。

## 4. RunEvent

```json
{
  "event_id": "evt_...",
  "event_type": "attempt.completed",
  "occurred_at": "2026-08-20T14:00:00Z",
  "tenant_id": "tenant_123",
  "run_id": "run_...",
  "task_id": "task_...",
  "attempt_id": "attempt_...",
  "sequence": 42,
  "idempotency_key": "provider:browserstack:session:...:completed",
  "actor": { "type": "system", "id": "browserstack-adapter" },
  "trace_id": "...",
  "correlation_id": "run_...",
  "causation_id": "evt_previous_...",
  "schema_version": 1,
  "data": {}
}
```

约束：

- 同一 Run 的 `sequence` 单调递增。
- Producer 重复投递相同 `idempotency_key` 不产生第二个业务事实。
- Schema 只做向后兼容扩展；破坏性变化升级 `schema_version`。
- 事件中只存小型结构化事实；大载荷进入 Blob 并用 ArtifactRef 引用。

MySQL 中持久化的 Chat、Agent 和 Test Run domain event 共享内部 envelope：`eventId`、`tenantId`、`projectId`、`aggregateType`、`aggregateId`、aggregate 内单调 `sequence`、`eventType`、`schemaVersion`、`correlationId`、`causationId`、`traceId`、`occurredAt`、`idempotencyKey` 与 typed payload。三类 aggregate 保持各自状态机。面向浏览器的 SSE 是授权后的领域投影，可省略内部 tenant/policy 字段，但必须保留 opaque `eventId` 和 `sequence`。

## 5. Provider Port

### AgentRuntime

```typescript
type AgentPurpose =
  | "knowledge_research"
  | "knowledge_enrichment"
  | "generate_test_ir"
  | "generate_candidate_patch"
  | "failure_analysis";

type AgentCapability =
  | "knowledge.read"
  | "knowledge.enrich"
  | "workspace.read"
  | "workspace.write"
  | "test_ir.generate"
  | "candidate_patch.generate";

interface AgentRuntimeCapabilitySet {
  runtimeProvider: string;
  purposes: AgentPurpose[];
  capabilities: AgentCapability[];
  features: {
    eventStream: boolean;
    interactiveResponses: boolean;
    cooperativeCancel: boolean;
    threadResume: boolean;
  };
}

interface AgentTask {
  taskId: string;
  attemptId: string;
  tenantId: string;
  projectId: string;
  actorId: string;
  purpose: AgentPurpose;
  instruction: string;
  inputRefs: Array<{ kind: string; id: string; revision: string; contentHash: string }>;
  requestedCapabilities: AgentCapability[];
  outputSchemaVersion: string;
  idempotencyKey: string;
}

interface RuntimePolicy {
  policyId: string;
  policyVersion: string;
  runtimeConfigRef: string;         // platform-owned, immutable; never user/repo config
  credentialBrokerRef: string;      // reference only; no secret value reaches the task
  modelRouteRef: string;            // separately governed model/auth egress
  sandbox: "read_only" | "workspace_write"; // service mode excludes full_access
  allowedCapabilities: AgentCapability[];
  allowedTools: string[];
  commandNetworkAllowlist: string[]; // applies only to model-controlled commands; model egress is separately governed
  webSearch: "disabled";
  allowedMcpServers: string[];       // Phase 1.5 only permits the named TAP Tool Gateway
  allowedSkills: string[];           // platform-pinned only
  allowedApps: string[];             // empty in service mode
  allowedPlugins: string[];          // empty in service mode
  allowedConnectors: string[];       // empty in service mode
  browserEnabled: false;
  computerUseEnabled: false;
  cloudTasksEnabled: false;
  shellEnvironmentPolicy: {
    inherit: "none";
    includeOnly: string[];
    ignoreDefaultExcludes: false;
    useShellProfile: false;
  };
  interactionMode: "headless_fail_closed" | "tap_brokered";
  budgets: {
    deadlineSeconds: number;
    maxTurns: number;
    maxTokens: number;
    maxToolCalls: number;
  };
  inputManifestHash: string;         // binds the immutable, authorized AgentTask.inputRefs
  retrievalProfileId?: string;
  corpusVersion?: string;
}

interface RuntimeHandle {
  runtimeProvider: string;
  externalThreadId?: string;
  externalTurnId?: string;
  attemptId: string;
  negotiated: AgentRuntimeCapabilitySet["features"];
}

interface InteractionResponse {
  interactionId: string;
  decision: "allow_once" | "deny";
  respondedBy: string;
  reason?: string;
}

interface AgentEvent {
  eventId: string;
  attemptId: string;
  traceId: string;
  sequence: number;
  occurredAt: string;
  schemaVersion: string;
  type: "started" | "progress" | "tool_requested" | "tool_completed" | "approval_requested" | "artifact_ready" | "cancel_requested" | "completed" | "failed" | "canceled" | "timed_out" | "unavailable";
  data: Record<string, unknown>; // normalized and redacted; never hidden reasoning
}

interface GeneratedArtifactEnvelope {
  artifactId: string;
  kind: "report" | "draft_test_ir" | "patch" | "code" | "enrichment" | "evidence_manifest";
  uri: string;
  contentHash: string;
  classification: string;
  inputRefs: Array<{ id: string; revision: string; contentHash: string }>;
  generator: {
    runtimeProvider: string;
    runtimeVersion: string;
    model: string;
    promptVersion: string;
    toolsetVersion: string;
    policyVersion: string;
  };
  validation: { status: "pending" | "passed" | "failed"; validatorVersion?: string; evidenceRefs: string[] };
}

interface AgentResult {
  status: "succeeded" | "failed" | "canceled" | "timed_out" | "unavailable";
  artifacts: GeneratedArtifactEnvelope[];
  findings: string[];
  usage: { inputTokens?: number; outputTokens?: number; costAmount?: number; currency?: string };
  externalThreadId?: string;
}

interface AgentRuntime {
  capabilities(): Promise<AgentRuntimeCapabilitySet>;
  start(task: AgentTask, policy: RuntimePolicy): Promise<RuntimeHandle>;
  events?(handle: RuntimeHandle, cursor?: string): AsyncIterable<AgentEvent>;
  respond?(handle: RuntimeHandle, response: InteractionResponse): Promise<void>;
  cancel?(handle: RuntimeHandle, reason: string): Promise<void>;
  result(handle: RuntimeHandle): Promise<AgentResult>;
}
```

约束：

- `AgentTask`、`RuntimePolicy`、Task/Attempt 必须在启动外部 Runtime 前持久化；Runtime thread/turn ID 只是 Provider 引用。
- Runtime 只能得到 `requestedCapabilities ∩ allowedCapabilities`，工具再与 `allowedTools` 求交；Capability 与工具名是两个独立维度，内容、Prompt 或 Provider 事件不能扩权。
- Adapter 必须先协商 `features`。Phase 1.5 的 headless SDK 路径使用 `interactionMode=headless_fail_closed`，对应 `approval_policy=never` 与最小 sandbox；越界请求直接失败。只有经验证且声明 `interactiveResponses=true` 的 Adapter 才能使用 `tap_brokered/respond`，发布 Artifact 的人工审批仍在 Runtime 之外完成。
- 服务模式禁止 `full_access`。凭据由可信 wrapper、workload identity 或 credential broker 持有，不能进入 Agent workspace、Prompt、Artifact 或模型控制的 Shell 环境。
- Runtime Pod 禁用自动 ServiceAccount token；projected identity 只显式挂入可信 sidecar。Agent/command 容器使用干净 runtime home 和平台固定配置，不加载个人 auth/config、repo `.codex` capability 配置或未批准插件。Artifact 上传和 TAP Tool 调用由可信 Broker/Gateway 完成。
- 每个输出先落 `GeneratedArtifactEnvelope` 并经过 Schema/Compiler/Test/Policy 验证；Agent 的 completed 事件不能替代 TAP validator 的 passed 结论。
- 未知 Provider 事件只保存为脱敏诊断信息，不能乐观映射为 `succeeded`。
- Codex 的具体接入与隔离规则见 [受控 Codex Agent Runtime](codex-agent-runtime.md)。

### Phase 1.5 Agent Job API

Phase 1.5 的公共 API 只开放只读 Research 与受控 Knowledge Enrichment；Test IR/代码生成在 Phase 2 具备相应 Schema、compiler 与 validator 后扩展，不复用 Chat turn 状态机。

```typescript
type Phase15AgentPurpose = "knowledge_research" | "knowledge_enrichment";
type AgentJobState =
  | "queued"
  | "running"
  | "cancel_requested"
  | "succeeded"
  | "failed"
  | "canceled"
  | "timed_out"
  | "unavailable";

interface AgentJobRequest {
  clientRequestId: string;
  purpose: Phase15AgentPurpose;
  instruction: string;
  resourceRefs: ResourceRef[];
  requestedCorpusVersion?: string;
  outputSchemaVersion: string;
}

interface AgentJobSummary {
  jobId: string;
  projectId: string;
  purpose: Phase15AgentPurpose;
  state: AgentJobState;
  activeAttemptId?: string;
  artifactRefs: string[];
  createdAt: string;
  completedAt?: string;
}

interface AgentJobEventEnvelope {
  eventId: string;
  sequence: number;
  jobId: string;
  attemptId?: string;
  traceId: string;
  occurredAt: string;
  schemaVersion: number;
  type: "job.queued" | "attempt.started" | "tool.completed" | "artifact.ready" | "job.cancel_requested" | "job.succeeded" | "job.failed" | "job.canceled" | "job.timed_out" | "job.unavailable";
  data: Record<string, unknown>;
}

interface EnrichmentReviewRequest {
  clientRequestId: string;
  expectedContentHash: string;
  decision: "approve_for_indexing" | "reject";
  reason?: string;
}
```

```text
POST /v1/projects/{projectId}/agent-jobs
GET  /v1/projects/{projectId}/agent-jobs
GET  /v1/agent-jobs/{jobId}
POST /v1/agent-jobs/{jobId}/cancel
GET  /v1/agent-jobs/{jobId}/events
POST /v1/agent-artifacts/{artifactId}/review
```

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    queued --> canceled
    queued --> unavailable
    running --> cancel_requested
    running --> succeeded
    running --> failed
    running --> timed_out
    running --> unavailable
    cancel_requested --> canceled
    cancel_requested --> failed
    cancel_requested --> timed_out
    succeeded --> [*]
    failed --> [*]
    canceled --> [*]
    timed_out --> [*]
    unavailable --> [*]
```

- 公共请求不能选择 Runtime、sandbox、工具、网络、tenant/group/ACL 或物理索引；BFF 从身份、Project Policy 和 purpose 生成内部 `AgentTask + RuntimePolicy`。
- Job `clientRequestId` 在同一 Project 内幂等；Review `clientRequestId` 在同一 Artifact 内幂等且相反决定产生冲突。浏览器断线不取消 Job；事件持久化并支持 `Last-Event-ID`，重放事件不重放副作用。
- cancel 先进入 `cancel_requested` 并停止新工具调用；若 Adapter 无 cooperative cancel，Dispatcher 终止隔离 Worker。Retry 创建新 Attempt，不覆盖旧事件或 Artifact。
- Enrichment 仅允许受限管理员/Indexer 服务角色创建，结果进入 staging Derivation Artifact；它不能直接发布 active corpus。
- `job.succeeded` 只表示 required Artifact 已由 Broker 封存且通过该 purpose 的确定性 Schema/Enrichment Validator；不表示 Artifact 已获管理员批准或已经进入 active corpus。
- Review 只接受已通过 Enrichment Validator 且 content hash 匹配的不可变 Artifact，并按当前 ACL/角色重新授权；`approve_for_indexing` 只向标准 Indexer 写入幂等 publish intent，不直接调用 AI Search。决定、actor、时间与 reason 进入 MySQL Audit。

### ExecutionProvider

```typescript
type ExecutionKind = "browser" | "device" | "api_contract";

interface ArtifactRef {
  artifactId: string;
  uri: string;                      // internal URI; never exposed directly to browser
  contentHash: string;
  mediaType: string;
  classification: string;
}

interface SecretRef {
  provider: "key_vault" | "workload_identity";
  reference: string;                // secret value is never present in the plan
  audience: string;
  version?: string;
}

interface CapabilitySet {
  provider: string;
  executionKinds: ExecutionKind[];
  browsers?: string[];
  platforms?: string[];
  devices?: string[];
  features: string[];
  maxConcurrency?: number;
  version: string;
}

interface ExecutionPlan {
  executionPlanId: string;
  tenantId: string;
  projectId: string;
  runId: string;
  taskId: string;
  attemptId: string;
  executionKind: ExecutionKind;
  sourceRevision: SourceRevisionRef;
  testAssetId: string;
  testAssetRevision: string;
  compiledArtifact: ArtifactRef;
  matrix: Record<string, string[]>;
  environment: string;
  evidencePolicy: Record<string, unknown>;
  providerOptionsRef?: string;
  deadlineAt: string;
  idempotencyKey: string;
}

interface ProviderAttempt {
  provider: string;
  externalAttemptId: string;
  submittedAt: string;
  idempotencyKey: string;
}

interface AttemptSnapshot {
  externalAttemptId: string;
  state: "queued" | "running" | "completed" | "failed" | "canceled" | "unknown";
  observedAt: string;
  providerSequence?: string;
  diagnostic?: Record<string, unknown>;
}

interface ProviderArtifact {
  externalArtifactId: string;
  kind: string;
  mediaType: string;
  sizeBytes?: number;
  contentHash?: string;
  downloadHandle: string;            // consumed only by trusted Result Collector
  occurredAt: string;
}

interface ExecutionProvider {
  capabilities(): Promise<CapabilitySet>;
  submit(plan: ExecutionPlan, credential: SecretRef): Promise<ProviderAttempt>;
  status(attempt: ProviderAttempt): Promise<AttemptSnapshot>;
  cancel(attempt: ProviderAttempt): Promise<void>;
  artifacts(attempt: ProviderAttempt): AsyncIterable<ProviderArtifact>;
}
```

端口不能暴露 DeepSeek Harness 的插件对象或 BrowserStack capability JSON。Provider 特有字段放入版本化 `provider_options`，领域层只读取经过声明的通用能力。

Provider 只返回原始状态和 artifact handle。可信 Result Collector 负责去重/排序、下载、脱敏、完整性校验、Blob 上传与 Evidence Manifest 构建；Provider `completed` 不等于 TAP Workflow success，只有 Manifest 封存和确定性 Quality Gate 完成后才能推进业务终态。

## 6. Evidence Manifest 与 Finding

每个 Attempt 对应一个不可变 Evidence Manifest：

```yaml
schemaVersion: 1
attemptId: attempt_123
source:
  repository: github:owner/repo
  commit: 0123456789abcdef
  testAssetId: test_checkout_happy_path
  testAssetRevision: 0123456789abcdef
runtime:
  provider: self-hosted-selenium
  externalId: session_456
  runnerImage: ghcr.io/example/tap-runner@sha256:...
artifacts:
  - kind: screenshot
    uri: azblob://tap-evidence/tenant/run/attempt/failure.png
    sha256: "..."
    classification: internal
    redactionStatus: completed
result:
  conclusion: failed
  exitReason: assertion_failed
```

若 Attempt 包含 Agent，还必须记录 model、prompt、tool、Agent Runtime 与 policy version。Manifest 只引用 Key Vault 中的 SecretRef，绝不保存明文秘密。

### Finding

每个 Finding 至少包含：

- `kind`：test_failure、flake、security、accessibility、agent_diagnosis、agent_suggestion。
- `severity` 与 `confidence`；确定性测试的 confidence 固定为 1。
- `source`：产生它的 Task、Attempt 和 Provider。
- `evidence_refs`：日志片段、截图、视频、测试用例或代码位置。
- `fingerprint`：跨 Run 关联同类问题的稳定指纹。
- `disposition`：open、accepted、suppressed、fixed、invalid。

Agent Finding 必须显式标记 `generated_by_agent=true`，且引用支持其判断的证据，不能伪装成测试事实。

## 7. 幂等与副作用

外部动作的幂等键模板：

```text
{tenant_id}:{run_id}:{task_id}:{attempt_no}:{action}:{action_version}
```

以下动作必须持久化 intent 后再执行：创建 GitHub Check、提交 BrowserStack session、启动 Agent Runtime、写 PR 评论、取消外部任务。执行成功后记录外部 ID；进程崩溃时由 Reconciler 查询外部状态，而不是盲目重放。

## 8. Retrieval Contract

```typescript
type SourceFamily = "doc" | "code" | "bdd" | "failure";
type ResourceMode = "required" | "preferred" | "scope";
type AnswerMode = "quick" | "deep";

interface ResourceRef {
  family: SourceFamily;
  sourceId: string;
  mode?: ResourceMode;             // defaults to preferred
  requestedRevision?: string;
  anchor?: StructuralAnchor;
}

// 浏览器、Agent 和其他消费者只允许提交检索意图与更窄的 scope。
interface PublicRetrievalQuery {
  query: string;
  answerMode?: AnswerMode;
  sources?: SourceFamily[];
  resourceRefs?: ResourceRef[];
  requestedEnvironment?: string;
  requestedCorpusVersion?: string;
  topK?: number;
}

interface ResolvedResourceRef {
  family: SourceFamily;
  sourceId: string;
  mode: ResourceMode;
  revision: string;
  sourceContentHash: string;
  anchor?: StructuralAnchor;
  aclDecisionId: string;
}

interface QueryPlan {
  queryPlanId: string;
  operationId: string;
  tenantId: string;
  projectId: string;
  plannerVersion: string;
  originalQuery: string;
  standaloneQuery: string;
  intent: "exact_lookup" | "qa" | "compare" | "explain" | "cross_source" | "follow_up";
  confidence: number;
  answerMode: AnswerMode;
  retrievalProfileId: string;       // server-selected versioned profile for this AnswerMode
  effectiveSourceFamilies: SourceFamily[];
  exactIdentifiers: Array<{ kind: string; value: string }>;
  effectiveResourceRefs: ResolvedResourceRef[];
  effectiveEnvironment?: string;
  effectiveCorpusVersion: string;
  candidateLimit: number;           // server-capped effective topK
  policyDecisionId: string;
  policyVersion: string;
  aclDigest: string;
  rawRequestHash: string;
  subqueries: Array<{ id: string; query: string; sourceFamilies: SourceFamily[] }>;
  createdAt: string;
}

interface ContextSnapshot {
  contextSnapshotId: string;
  operationId: string;
  tenantId: string;
  chatId?: string;
  turnId?: string;
  projectId: string;
  policyDecisionId: string;
  policyVersion: string;
  aclDigest: string;
  layers: Array<{
    kind: "project_policy" | "project_context" | "recent_turns" | "conversation_summary" | "current_turn";
    refIds: string[];
    contentHash: string;
    tokenCount: number;
  }>;
  summaryLineage?: { summaryId: string; sourceTurnIds: string[]; sourceContentHashes: string[]; summarizerVersion: string; authorizedAt: string };
  createdAt: string;
}

// 只能由 BFF/Policy 层依据 Entra 身份和服务端事实构造。
interface RetrievalPolicyContext {
  tenantId: string;
  projectId: string;
  actor: { userId: string; allowedGroupIds: string[]; roles: string[] };
  allowedClassifications: string[];
  allowedEnvironments: string[];
  aclDigest: string;
  policyVersion: string;
  decisionId: string;
}

// 仅在服务内部传递，不能作为公共 HTTP DTO 反序列化。
interface InternalRetrievalRequest {
  policy: RetrievalPolicyContext;
  queryPlan: QueryPlan;
  contextSnapshot: ContextSnapshot;
}

type StructuralAnchor =
  | { type: "document"; headingPath?: string[]; page?: number; bbox?: number[]; startOffset?: number; endOffset?: number }
  | { type: "code"; repo: string; path: string; symbol?: string; lineStart: number; lineEnd: number }
  | { type: "bdd"; featureId: string; scenarioId?: string; stepId?: string }
  | { type: "openapi"; method: string; path: string; jsonPointer: string }
  | { type: "failure"; incidentId: string; runId?: string; timeStart?: string; timeEnd?: string };

interface SourceRevisionRef {
  sourceId: string;
  sourceType: string;
  revisionKind: "git_commit" | "blob_version" | "mysql_version";
  revision: string;
  sourceContentHash: string;
  anchor: StructuralAnchor;
}

interface ChunkRecord {
  chunkId: string;                 // immutable snapshot identity
  logicalChunkId: string;          // stable identity across revisions
  rootId: string;
  parentId?: string;
  adjacentChunkIds?: string[];
  source: SourceRevisionRef;
  chunkContentHash: string;
  contentRole: "source" | "generated_summary";
  derivedFromChunkIds?: string[];
  derivation?: {
    derivationKey: string;
    generatorKind: string;
    runtimeVersion?: string;
    modelSnapshot: string;
    promptVersion: string;
    toolsetVersion?: string;
    outputSchemaVersion: string;
    decodingProfile: string;
    redactionPolicyVersion: string;
  };
  ordinal: number;
  tokenCount: number;
  parserVersion: string;
  chunkerVersion: string;
  pipelineVersion: string;
  aclVersion: number;
}

interface Citation {
  citationId: string;              // opaque resolver ID, not a source URL
  evidenceLabel: string;           // for example S1
  chunkId: string;
  logicalChunkId: string;
  source: SourceRevisionRef;
  chunkContentHash: string;
  contentRole: "source" | "generated_summary";
  derivedFromChunkIds?: string[];
}

interface RetrievalResponse {
  traceId: string;
  queryPlanId: string;
  contextSnapshotId: string;
  corpusVersion: string;
  retrievalProfileId: string;
  degradedMode: boolean;
  degradationReasons?: string[];
  hits: Array<{
    indexFamily: SourceFamily;
    physicalIndex: string;
    chunkId: string;
    logicalChunkId: string;
    parentId?: string;
    title?: string;
    content: string;
    language?: string;
    source: SourceRevisionRef;
    chunkContentHash: string;
    citationId: string;
    scores: { exact?: number; bm25?: number; vector?: number; rrf?: number; rerank?: number };
    aclDecisionId: string;
    schemaVersion: string;
    embeddingModelVersion: string;
    rerankerModelVersion?: string;
  }>;
}

interface RetrievalAnswerResponse {
  traceId: string;
  queryPlanId: string;
  contextSnapshotId: string;
  corpusVersion: string;
  retrievalProfileId: string;
  degradedMode: boolean;
  degradationReasons?: string[];
  answer: string;
  abstained: boolean;
  abstentionReason?: "insufficient_evidence" | "conflicting_sources" | "revision_mismatch";
  claims: Array<{
    claimId: string;
    text: string;
    citationIds: string[];
  }>;
  citations: Citation[];
}
```

- 公共请求中不得出现 `tenantId`、`projectId`、group IDs、classification、任意 ACL/filter 表达式或物理索引名。BFF 从 Entra 与 Policy 服务构造 `RetrievalPolicyContext`；`requested*` 只能与服务端允许范围取交集，不能扩大权限。
- `ResourceRef.mode` 的语义固定为：所有 `required` 资源采用 AND coverage，每个都必须贡献至少一条最终 Citation，否则拒答；多个 `scope` 资源采用授权结构子树的 union 作为搜索边界；`preferred` 独立加权但允许补充其他授权来源。默认 `preferred`。资源搜索建议、revision 解析和最终读取都先执行 ACL。
- QueryPlan 是 Policy 求交与 Planner 之后的唯一有效、不可变执行计划，不是浏览器可提交的权限对象。它保存已解析的 immutable revision/hash、effective scope/corpus/environment、server-capped candidate limit、profile、bounded subqueries、Policy/ACL digest 与 raw request hash；Internal Retrieval 不再同时消费可能冲突的原始 DTO。重新规划创建新的 QueryPlan ID，不覆盖旧 Trace。
- 执行前必须满足 `policy.decisionId == queryPlan.policyDecisionId == contextSnapshot.policyDecisionId`，且三者的 tenant/project、policyVersion、aclDigest 完全一致，`queryPlan.operationId == contextSnapshot.operationId`。任何缺失、失配或过期都在访问 Search 前 fail closed，并基于当前 Policy 生成新的 QueryPlan/Context Snapshot；禁止就地改写旧对象。所有 Retrieval/Answer 响应必须返回本次两个 ID。
- `quick/deep` 是 `AnswerMode`，只表示用户期望；服务端把它映射到版本化 `retrievalProfileId` 并固化在 QueryPlan，浏览器不能指定物理 Profile。
- Context Snapshot 绑定一次 retrieval operation；Chat 路径额外绑定 `chatId/turnId`，Agent/API 路径无需伪造会话。它只保存分层上下文的 refs/hash/token 与 summary lineage，不把秘密、完整 Prompt 或未经授权原文复制到元数据。每个 turn 按当前 Policy 重新授权 summary lineage；若来源撤权、删除或 hash 变化，就排除失效输入并生成新 summary/snapshot。Conversation summary 只能帮助连续性和指代消解，不能作为事实 Citation。
- `topK`、resource 数量、query 长度、分解次数和 context budget 均由服务端 profile 限幅；公共字段是请求偏好，不是资源或排名控制权。
- classification 由策略层转换为明确的允许集合，不能做字符串大小比较；environment 的默认语义是 `global OR requested environment`，且 requested environment 必须在允许集合中。
- 每个命中返回稳定 index family、实际 physical index、chunk/logical ID、不可变 `SourceRevisionRef`、score components、ACL decision、corpus/schema/profile/model version。物理索引从 `*-v1` 蓝绿升级到 `*-v2` 不破坏公共契约。
- `logicalChunkId` 在同一结构位置跨 revision 稳定；`chunkId` 随 source revision/content/chunker version 变化。完整生成规则见 [切片与溯源设计](chunking-and-provenance.md)。
- `chunkId` 作为 Azure AI Search document key 使用 `h_` + SHA-256 lowercase hex；不得把带冒号的 digest、URI 或路径直接作为 key。
- 非拒答结果中的每个实质 claim 必须引用至少一个当前 context 中的 `citationId`。Citation Resolver 将其解析到不可变 revision、structured anchor、`sourceContentHash` 与 `chunkContentHash`；浏览器不直接使用内部 `sourceUri`。证据不足、来源冲突或 revision 不一致时返回结构化拒答原因。
- 代码命中返回原语言 symbol/AST chunk；不得为了统一格式把源码转成 Markdown。
- Parent/Child、依赖图、facet/count 和缓存均必须再次应用同一 ACL filter；不同 ACL 的 child 不得汇总进同一个 parent summary。
- ACL/Policy 服务不可用时 fail closed；秘密/PII 必须在 Embedding 前脱敏。
- Retrieval Trace 必须绑定 tenant/project/actor 与 ACL digest；`traceId` 不具有授权语义。Trace/Inspector 读取需要重新授权、必要脱敏和审计，撤权后不能借旧 trace 绕过当前 ACL。
- Index schema 与 embedding/reranker version 一起版本化；不同向量空间不混合查询。

## 9. Knowledge Chat Contract

```typescript
interface ChatSession {
  chatId: string;
  projectId: string;               // route + current authorization decide visibility
  parentChatId?: string;
  branchedFromTurnId?: string;
  title: string;
  defaultSourceScope: SourceFamily[];
  defaultEnvironment?: string;
  createdAt: string;
  updatedAt: string;
  latestTurnId?: string;
}

interface ChatTurnRequest {
  clientRequestId: string;
  message: string;
  answerMode?: AnswerMode;          // defaults to quick
  sourceScope?: SourceFamily[];
  resourceRefs?: ResourceRef[];
  requestedEnvironment?: string;
  requestedCorpusVersion?: string;
}

interface QueuedChatMessage {
  messageId: string;
  clientRequestId: string;
  afterTurnId: string;
  position: number;
  message: string;
  answerMode?: AnswerMode;
  sourceScope?: SourceFamily[];
  resourceRefs?: ResourceRef[];
  requestedEnvironment?: string;
  requestedCorpusVersion?: string;
  createdAt: string;
  version: number;                  // optimistic concurrency for edit/reorder
}

interface ChatTurnSummary {
  turnId: string;
  clientRequestId: string;
  state: ChatTurnState;
  degradedMode: boolean;
  degradationReasons?: string[];
  corpusVersion: string;
  retrievalProfileId: string;
  queryPlanId?: string;
  contextSnapshotId?: string;
  traceId?: string;
  createdAt: string;
  completedAt?: string;
}

interface ChatTurnSnapshot {
  turn: ChatTurnSummary;
  answerSoFar: string;
  citations: Citation[];
  lastSequence: number;
  snapshotVersion: number;
}

interface CursorPage<T> {
  items: T[];
  nextCursor?: string;
}

type ChatTurnState =
  | "queued"
  | "running"
  | "stopping"
  | "completed"
  | "abstained"
  | "canceled"
  | "failed";

type ChatStreamEvent =
  | { type: "turn.started"; payload: { state: "running" } }
  | { type: "context.assembled"; payload: { contextSnapshotId: string; tokenCount: number } }
  | { type: "query.plan_ready"; payload: { queryPlanId: string; answerMode: AnswerMode; sourceFamilies: SourceFamily[] } }
  | { type: "stage.started"; payload: { stage: string } }
  | { type: "stage.completed"; payload: { stage: string; durationMs: number } }
  | { type: "retrieval.hits_ready"; payload: { traceId: string; authorizedHitCount: number } }
  | { type: "rerank.completed"; payload: { candidateCount: number; durationMs: number } }
  | { type: "answer.delta"; payload: { text: string } }
  | { type: "citation.resolved"; payload: { citation: Citation } }
  | { type: "turn.completed"; payload: { answer: RetrievalAnswerResponse } }
  | { type: "turn.abstained"; payload: { answer: RetrievalAnswerResponse } }
  | { type: "turn.degraded"; payload: { reason: string; availableStages: string[] } } // nonterminal advisory
  | { type: "turn.canceled"; payload: { partialAnswerRetained: boolean } }
  | { type: "turn.failed"; payload: { code: string; retryable: boolean } };

interface ChatEventEnvelope {
  eventId: string;                 // opaque identity; never used for lexical ordering
  sequence: number;                // monotonic within a turn; ordering/resume key
  chatId: string;
  turnId: string;
  occurredAt: string;
  schemaVersion: number;
  event: ChatStreamEvent;
}

interface StreamResetRequired {
  code: "stream_reset_required";
  turnId: string;
  snapshotUrl: string;
}
```

API 基线：

```text
POST   /v1/chats
GET    /v1/projects/{projectId}/chats?cursor=&limit=
GET    /v1/chats/{chatId}?cursor=&limit=
POST   /v1/chats/{chatId}/turns
POST   /v1/turns/{turnId}/fork
POST   /v1/turns/{turnId}/cancel
GET    /v1/turns/{turnId}
GET    /v1/chats/{chatId}/queue
POST   /v1/chats/{chatId}/queue
PATCH  /v1/chats/{chatId}/queue/{messageId}
DELETE /v1/chats/{chatId}/queue/{messageId}
GET    /v1/turns/{turnId}/events?afterSequence=
POST   /v1/turns/{turnId}/feedback
GET    /v1/citations/{citationId}
GET    /v1/retrieval/traces/{traceId}?cursor=&limit=
```

- 浏览器只连接 TAP BFF，不能直连 Azure AI Search 或 LiteLLM。BFF 为 Chat turn 注入与 Retrieval Contract 相同的可信 `RetrievalPolicyContext`。
- `clientRequestId` 在同一 chat 内幂等；重复提交返回同一 `turnId`。`GET /turns/{turnId}` 返回 materialized `ChatTurnSnapshot`，随后 SSE 只从 `afterSequence` 追尾。SSE wire `id` 是十进制 `sequence`，因此自动 `Last-Event-ID` 与显式 query cursor 同义；payload 的 `eventId` 仍是不透明身份。排序、幂等归并和恢复使用显式 `sequence`，重放事件不得重放检索、模型或其他副作用。
- Event Projection 必须把 provider token 合并为有界、稳定、可重放的 `answer.delta`；禁止每 token 一次 MySQL transaction/SSE event。若连接时 cursor 已超出 replay 数量/字节上限，SSE endpoint 在建立流之前返回 HTTP `409 application/json`，body 符合 `StreamResetRequired`；若已建立的流检测到不可恢复缺口，则发送一次 `event: stream.reset_required`（data 同一结构）后关闭。客户端必须重新 `GET` snapshot，再以新的 `lastSequence` 建立 SSE tail，不能无限从头回放。
- Chat history、Project chat list 与 Retrieval Trace 必须使用 cursor pagination 和服务端 limit 上限；主 SSE 只传 Trace ID/计数/摘要，候选正文按需读取并重新授权。
- cancel 是显式状态迁移：客户端先请求停止，收到 `turn.canceled` 后才能立即发送纠偏 turn。部分文本必须标记为已中断。
- 运行中的排队消息通过 chat-scoped Queue API 创建和列出，并用 `afterTurnId` 固定依赖；保持独立 ID、顺序和内容，不得静默拼入当前 query。编辑旧消息会创建新 turn，旧 turn/trace 保持不可变。
- Fork 从一个不可变 turn 创建新的 `ChatSession`，记录 `parentChatId/branchedFromTurnId`；它可以更改问题、scope 或 AnswerMode，由服务端产生新的 Retrieval Profile/QueryPlan，但不能改写原 session、answer、QueryPlan、Context Snapshot 或 Trace。
- Project policy、近期 turns、conversation summary 与本轮 evidence 分层组装。上下文达到阈值时，服务端生成带 source turn lineage 的新 summary 派生物并重新授权输入；这不是 Phase 1 用户命令，也不删除原始消息。权限、强制规则和事实 Citation 不能只存在于 summary。
- `turn.degraded` 是运行中的非终态告警；最终仍必须收到 completed/abstained/canceled/failed 之一，且 `turn.completed` 中的 `degradedMode` 与 reasons 固化本次降级事实。
- `turn.completed` 的 answer 必须 `abstained=false`；`turn.abstained` 携带 `abstained=true` 的完整 AnswerResponse，保留冲突/证据不足的引用与结构化原因。
- `stage.*` 只描述可观察的系统动作、数量、耗时和状态，不传输隐藏思维链、系统提示词或内部推理文本。
- `citationId`、`traceId`、`chatId` 和 `turnId` 都不是访问凭证。恢复会话、展开历史答案、解析引用或读取 Trace 时，按当前身份/ACL 重新授权并审计；撤权后 fail closed。
- Markdown、代码块、链接和 source preview 必须经过 allowlist sanitizer 与安全 URL resolver；禁止模型生成的任意 URL 直接成为可点击引用。

页面行为与验收标准见 [TAP Knowledge Chat](knowledge-chat-ui.md)。
