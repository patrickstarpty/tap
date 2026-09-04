# Athena 知识与 Web 自动化平台核心契约

本页把 [RFC-009](../proposals/2026-09-04-rfc-009-athena-knowledge-web-automation-platform.md) 中跨模块、必须在实现期间保持稳定的契约集中列出。它不是手写的最终 OpenAPI；HTTP DTO 仍由 Backend 定义并生成 TypeScript。若本页与 RFC-009 冲突，以 RFC 为准。

## 1. 身份与范围

```python
class IdentityMode(StrEnum):
    VALIDATION = "validation"
    PRODUCT = "product"

@dataclass(frozen=True, slots=True)
class AnonymousContext:
    scope_kind: Literal["ANONYMOUS"]
    enterprise_id: str

@dataclass(frozen=True, slots=True)
class PlatformScopeContext:
    scope_kind: Literal["PLATFORM"]
    enterprise_id: str
    actor_id: str
    identity_mode: IdentityMode

@dataclass(frozen=True, slots=True)
class ProjectScopeContext:
    scope_kind: Literal["PROJECT"]
    enterprise_id: str
    project_id: str
    actor_id: str
    identity_mode: IdentityMode

IdentityContext = AnonymousContext | PlatformScopeContext | ProjectScopeContext

class ScopeProvider(Protocol):
    async def current(self, request: RequestFacts) -> IdentityContext: ...

class AuthorizationPolicy(Protocol):
    async def authorize(
        self,
        scope: IdentityContext,
        action: str,
        resource: ResourceRef,
    ) -> AuthorizationDecision: ...
```

约束：

- 客户端不能提交或覆盖 actor、role、enterprise 或权威 Project scope。
- Project 路径参数必须与 `ProjectScopeContext.project_id` 精确一致，否则返回 `scope-mismatch`。
- Project Repository 的每个业务查询都强制 `project_id`；Platform scope 不隐式授予 Project 内容访问。
- 每次模型、对象存储、Recorder、Jenkins 与 Artifact Gateway 副作用前重新授权。
- V0–VG 只装配固定 `ValidationScopeProvider`；P0 使用 Session/Membership Adapter 替换它，核心应用服务不增加第二套 RBAC 分支。

## 2. Project 事件信封

```python
@dataclass(frozen=True, slots=True)
class ProjectEventEnvelope:
    event_id: str
    event_type: str
    schema_version: int
    occurred_at: datetime
    scope_kind: Literal["PROJECT"]
    enterprise_id: str
    project_id: str
    actor_id: str
    identity_mode: Literal["validation", "product"]
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    correlation_id: str
    causation_id: str | None
    idempotency_key: str
    payload: Mapping[str, object]
```

领域状态、Audit、Domain Event 与 Outbox 在同一个 MySQL 事务内提交。消费者以业务 `idempotency_key` 及 aggregate version/sequence 去重。Redis 只传输可重建唤醒，不是事件事实源；浏览器事件是授权后闭集投影，不原样透传内部 payload、Prompt、Secret 或 Provider 事件。

首个 major version 的 Project 事件及最小 payload 固定如下。`*Id` 都必须与 Envelope 的 Project/aggregate 一致；payload 不包含 Secret、原文或 Provider credential。

| event type                                  | aggregate          | 最小 payload / 消费幂等键                                                                 |
| ------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------- |
| `knowledge.document-revision.accepted`      | DocumentRevision   | `sourceId, documentId, revisionId, contentHash` / `revisionId:ingest`                     |
| `knowledge.document-revision.ready`         | DocumentRevision   | `revisionId, chunkManifestDigest, projectionDigest` / `revisionId`                        |
| `knowledge.graph-snapshot.requested`        | GraphSnapshot      | `snapshotId, sourceRevisionIds[], extractionProfileDigest` / snapshot                     |
| `knowledge.graph-snapshot.ready`            | GraphSnapshot      | `snapshotId, graphDigest, evidenceDigest` / snapshot                                      |
| `conversation.turn.requested`               | Turn               | `conversationId, turnId, inputSnapshotDigest` / turn                                      |
| `conversation.turn.completed`               | Turn               | `turnId, answerEvidenceSnapshotId, answerEvidenceSnapshotDigest, outcome` / turn          |
| `test-plan.generation.requested`            | TestPlanRevision   | `revisionId, inputSnapshotDigest, answerEvidenceSnapshotDigest, requestDigest` / revision |
| `test-plan.revision.published`              | TestPlanRevision   | `revisionId, contentDigest, validationDigest` / revision                                  |
| `automation.generation.requested`           | AutomationRevision | `revisionId, inputSnapshotDigest, answerEvidenceSnapshotDigest, requestDigest` / revision |
| `automation.revision.published`             | AutomationRevision | `revisionId, testIrDigest, bundleManifestDigest` / revision                               |
| `automation.debug-execution.requested`      | DebugExecution     | `debugExecutionId, draftDigest, environmentRevisionId` / execution                        |
| `automation.debug-execution.status-changed` | DebugExecution     | `debugExecutionId, from, to, sequence` / execution + sequence                             |
| `automation.debug-execution.completed`      | DebugExecution     | `debugExecutionId, outcome, evidenceManifestDigest` / execution                           |
| `recorder.session.requested`                | RecorderSession    | `sessionId, environmentRevisionId, policyDigest` / session                                |
| `recorder.session.completed`                | RecorderSession    | `sessionId, eventManifestDigest, outcome` / session                                       |
| `execution.run.requested`                   | ExecutionRun       | `runId, submissionKey, configurationManifestDigest` / run                                 |
| `execution.run.status-changed`              | ExecutionRun       | `runId, from, to, sequence, observationId` / run + sequence                               |
| `execution.run.completed`                   | ExecutionRun       | `runId, outcome, evidenceStatus, resultManifestDigest` / run                              |

每个消费者声明接受的 `schema_version`、业务幂等键和 retry/dead-letter 策略；未知 major、缺字段或跨 Project payload 进入可审计 dead-letter，不能 ack 后静默丢弃。状态改变事件必须与对应状态、Audit 和 Outbox 同事务；完成事件不能在必需 Manifest 尚未持久化时发出。SSE Schema 是这些内部事件的授权闭集投影，单独生成版本化 JSON Schema。

## 3. Knowledge 与 Conversation

`KnowledgeSource` 是 Project 内的逻辑容器，一个 Source 拥有一个或多个稳定 Document，每个 Document 拥有不可变 Revision。Knowledge 资源身份由 `project_id + source_id + document_id + revision_id + content_hash` 固定，Document 去重唯一性只在 `(project_id, dedupe_key)` 内成立；Chunk 另有跨 revision 稳定的 `logical_chunk_id` 与不可变 `chunk_id`。Citation 必须同时绑定 source/document revision、chunk、anchor、原文 digest 与回答时的授权快照。

Legacy 数据迁移为每个现有 Document 创建同 Project Source，保留 Document ID，并把历史 `source_id=document_id` 显式映射到新 Source ID。Milvus 新版本 collection 使用 canonical `enterprise_id/project_id/source_id`；旧 `tenant_id` 是迁移输入而非公共或新物理契约。

```python
class ModelGateway(Protocol):
    async def catalog(self, scope: ProjectScopeContext) -> ModelCatalog: ...
    async def chat(self, request: ChatModelRequest) -> ChatModelResult: ...
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult: ...
    async def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResult: ...
```

Knowledge、Graph、Test Plan 与 Automation generation 共用一个 `ModelGateway` 和 LiteLLM Adapter/conformance suite；模块自己的 Schema Validator 位于 Gateway 之外。Alias 解析、超时、脱敏、实际 provider/model 审计和错误映射不得各自复制。RFC-006 的直接 Codex CLI `AnswerGenerationPort` 只属于既有 loopback Demo，不是 V1 Adapter，也不能作为绕过该 Gateway 的第二模型出口；若保留，必须位于显式 legacy-loopback composition，不能被默认/Validation/Product runtime 导入或挂载 RFC-009 Project API。架构测试必须证明 `athena_runtime.py` 和四类 V1 调用者均无法解析该 Adapter，且默认 composition 对 `ATHENA_ANSWER_BACKEND=codex` fail closed。

AI Agent 与 Skill 都是服务器批准、版本化、不可执行的 Catalog 资源。`AgentRevision` 固定 system instruction template、允许的领域命令和输出 Schema；`SkillRevision` 固定指令/模板与适用任务。Validation 配置先提供只读 seed，P0 后由 Project Admin 管理。客户端只能提交可见 Revision ID，服务端重新解析并授权；任意插件代码、工具 URL、Secret 或未登记 Prompt 不进入当前合同。

```typescript
interface CreateTurnRequest {
  clientRequestId: string;
  message: string;
  modelAlias: string;
  knowledgeSourceIds: string[];
  aiAgentRevisionId?: string;
  skillRevisionIds: string[];
}

interface TurnInputSnapshot {
  projectId: string;
  actorId: string;
  identityMode: "validation" | "product";
  modelAlias: string;
  sourceRevisions: Array<{
    sourceId: string;
    documentId: string;
    revisionId: string;
    contentHash: string;
  }>;
  aiAgentRevisionId?: string;
  skillRevisionIds: string[];
  retrievalPolicyDigest: string;
}

interface RetrievalSummary {
  status: "COMPLETED" | "FAILED" | "NOT_RUN";
  retrievalProfileId: string;
  projectionVersion: string;
  candidateCount: number;
  selectedEvidenceCount: number;
}

interface CitationSnapshotEntry {
  citationId: string;
  sourceId: string;
  documentId: string;
  revisionId: string;
  chunkId: string;
  anchorDigest: string;
  contentDigest: string;
}

interface TurnAnswerEvidenceSnapshot {
  answerEvidenceSnapshotId: string;
  turnId: string;
  inputSnapshotDigest: string;
  answerDigest?: string;
  retrievalSummary: RetrievalSummary;
  graphContextStatus:
    "APPLIED" | "NOT_READY" | "FAILED" | "UNAVAILABLE" | "NOT_SELECTED";
  graphSnapshotId?: string;
  citationSnapshot: CitationSnapshotEntry[];
}
```

空白 New Chat 不落库；首条消息同事务创建 Conversation 与 Turn。接受 Turn 时，服务端先解析并授权 Source/Revision、Agent、Skill 和模型，再在同一事务写入不可变 `TurnInputSnapshot` 与 `conversation.turn.requested`；事件的 `inputSnapshotDigest` 只对该输入快照的 canonical 内容求值。检索、Graph、回答和 Citation 核验完成后，完成事务另写不可变 `TurnAnswerEvidenceSnapshot`，并让 `conversation.turn.completed` 同时引用其 ID 与 digest。输入快照绝不追加检索结果，回答/证据快照也不反向改写输入选择。SSE `id` 使用单调 sequence，`Last-Event-ID` 从下一事件恢复；取消、失败或重试创建可追溯事实，不覆盖旧事件。

`TurnAnswerEvidenceSnapshot.graphContextStatus=APPLIED` 时必须保存本轮实际查询的 active `graphSnapshotId`；其他状态不得伪造 Snapshot ID。这样审计可以区分“未使用图谱”“图谱不可用”和“已查询但没有相关关系”。失败或证据不足也必须保存闭合的 retrieval/Graph 状态和空或已验证的 Citation Snapshot，不能通过修改 Input Snapshot 表达处理进度。

Graph Snapshot、Node、Edge 与 Evidence 均带 Project 和来源 Revision。`EXTRACTED` Edge 必须有可解析 Evidence；`INFERRED` Edge 还必须有输入 Fact IDs 与推导 provenance。只对 active、已授权 Snapshot 做最多两跳的有界扩展。

## 4. Test Plan、BDD 与 Revision

```typescript
type RevisionStatus = "DRAFT" | "VALIDATING" | "PUBLISHED" | "SUPERSEDED";
type IdentityOrigin = "VALIDATION" | "PRODUCT";

interface TestPlanRevisionRef {
  testPlanId: string;
  revisionId: string;
  version: number;
  status: RevisionStatus;
  origin: IdentityOrigin;
  contentDigest: string;
}

interface TestPlanBddStep {
  testPlanStepId: string;
  keyword: "Given" | "When" | "Then" | "And" | "But";
  text: string;
}

interface AutomationBddStep {
  automationBddStepId: string;
  keyword: "Given" | "When" | "Then" | "And" | "But";
  text: string;
  implementationActionIds: string[];
}

interface StepMapping {
  testPlanRevisionId: string;
  testPlanStepId: string;
  automationRevisionId: string;
  automationBddStepId: string;
}
```

AI 只创建 Draft；确定性 Schema、Citation、BDD 和完整性门禁通过并由用户明确发布后，才形成不可变 Published Revision。Published/Superseded 不可编辑；后续修改必须 fork 新 Draft。并发更新通过 `If-Match`，冲突返回 `409 revision-conflict`。

生成 Draft 必须显式保存 Citation、Assumption、Unknown 和 Coverage Gap；Graph `INFERRED` 只能进入 Assumption，不能冒充来源事实。Test Design 与 Automation generation 请求必须同时固定同一 Turn 的 `inputSnapshotDigest` 和 `answerEvidenceSnapshotDigest`；服务端重新校验两者的 Turn/Project 归属和 digest 后才可生成，不能用其中一个替代另一个。

## 5. Automation、Test IR 与严格 1:1

```typescript
type TestIrActionKind =
  | "navigate"
  | "go_back"
  | "reload"
  | "click"
  | "fill"
  | "press"
  | "select_option"
  | "check"
  | "uncheck"
  | "upload_file"
  | "wait_for_url"
  | "wait_for_element"
  | "wait_for_response"
  | "assert_visible"
  | "assert_text"
  | "assert_value"
  | "assert_url"
  | "assert_download"
  | "call_fixture";

interface TestIrAction {
  actionId: string;
  automationBddStepId: string;
  ordinal: number;
  kind: TestIrActionKind;
  target?: LocatorRef;
  value?: LiteralValue | ParameterRef | SecretRef;
  waitPolicy?: WaitPolicy;
  assertion?: AssertionSpec;
}

interface CodeBundleManifest {
  automationId: string;
  revisionId: string;
  testIrDigest: string;
  generatorVersion: string;
  runnerImageDigest: string;
  playwrightBundleSha256: string;
  parameterSchemaSha256: string;
  lineMappingSha256: string;
}
```

每个 executable BDD Step 在发布时至少映射一个 Action，不能有 orphan Action。BDD Step、Action 与生成代码行双向可追溯。Playwright TypeScript Bundle 是 canonical Test IR 的确定性制品；依赖排序、归档顺序和 canonical JSON 固定，时间戳不参与 digest。

Test Plan 与 Automation 可以不关联；关联后严格双向 `1:1`。数据库对 `(project_id, test_plan_id)` 和 `(project_id, automation_id)` 分别建立唯一约束。关联冲突返回 `409 association-conflict`，缺少兼容步骤映射返回 `409 automation-mapping-required`。

Validation Revision 在 P0 后只允许历史读取或 fork；新 `PRODUCT` Revision 保存 `adopted_from_revision_id` 并重新经过发布、Bundle、Secret 和 Execution Target 门禁。

## 6. Recorder

```python
class RecorderPort(Protocol):
    async def allocate(self, request: RecorderAllocationRequest) -> RecorderAllocation: ...
    async def capture(self, session_id: str, event: CapturedEvent) -> int: ...
    async def stop(self, session_id: str) -> CapturedEventManifest: ...
    async def cleanup(self, session_id: str, reason: CleanupReason) -> None: ...
```

Stream ticket 绑定 actor/project/session、single-use、TTL 不超过 60 秒并校验精确 Origin。断线可以丢弃旧视频帧，不能丢失已经确认的捕获事件。事件清洗固定删除 mousemove/focus/重复 click，连续 fill 只保留最终值；等待必须转成条件，不自动生成 fixed sleep。

敏感输入只产生 `SecretRef`，原值不能进入 Captured Event、BDD、Test IR、生成代码、模型 Context、日志或截图 metadata。Recorder 只输出 Draft Proposal，不能直接发布 Automation。

Validation 阶段已经需要最小 Secret 解析设施，不能等到 P1 才出现。它是平台安全设施而非业务 Provider Port：

```python
class SecretLeaseResolver(Protocol):
    async def acquire(
        self,
        scope: ProjectScopeContext,
        secret_ref: SecretRef,
        purpose: Literal["RECORDER", "DEBUG", "PROVIDER", "CALLBACK_VERIFY"],
        worker_lease_id: str,
        ttl_seconds: int,
    ) -> EphemeralSecretLease: ...

    async def revoke(self, lease_id: str) -> None: ...
```

Validation Adapter 只解析服务端 allowlist 中的验证 Secret；P0/P1 再替换为 Project 管理和 envelope encryption。两者都要求 Project、用途、Worker lease 和短 TTL 绑定，任务结束/超时/崩溃由 Reconciler 撤销，并对日志、Trace、Problem Details、模型请求和制品执行同一 redaction contract。

## 7. Execution Run 与 Attempt

```typescript
type OperationStatus =
  "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "TIMED_OUT";
type TestOutcome = "NOT_RUN" | "PASSED" | "FAILED" | "INCONCLUSIVE";
type EvidenceStatus = "PENDING" | "COMPLETE" | "INCOMPLETE";
type SubmissionStatus =
  "NOT_SUBMITTED" | "SUBMITTING" | "SUBMITTED" | "SUBMIT_UNKNOWN";

interface RunConfigurationManifest {
  runId: string;
  rerunOfRunId?: string;
  attemptNo: 1;
  automationRevisionId: string;
  automationBundleSha256: string;
  testPlanIdAtRun?: string;
  testPlanRevisionIdAtRun?: string;
  linkVersionAtRun?: number;
  stepMappingDigest?: string;
  environmentRevisionId: string;
  executionTargetRevisionId: string;
  credentialBindingProfileRevisionId: string;
  runnerImageDigest: string;
  pipelineDigest: string;
  agentLabel: string;
}
```

Run 创建事务重读当前关联，验证 Published Revision、origin、mapping 与 Bundle digest，并冻结无 Secret 的 Run Configuration Manifest。`testPlanIdAtRun`、`testPlanRevisionIdAtRun`、`linkVersionAtRun` 与 `stepMappingDigest` 必须全空或全非空。当前路线一个 Run 恰好拥有一个 append-only Attempt，`attempt_no=1`，唯一 `submission_key = "{run_id}:1"`；重跑必须创建带 `rerunOfRunId` 的新 Run。任何配置或 Revision 改变也创建新 Run，不能修改旧 Run。

正式 Run 只执行 Published Automation Revision。Draft Debug Execution 保存独立 digest，不创建正式 Run、不调用 Jenkins、不投影 Test Plan。

```python
class ExecutionProvider(Protocol):
    async def verify_connection(
        self, target: ExecutionTargetRevision
    ) -> VerificationResult: ...
    async def submit(self, request: ExecutionRequest) -> ProviderRunRef: ...
    async def reconcile_submission(
        self,
        target: ExecutionTargetRevision,
        submission_key: str,
    ) -> SubmissionLookup: ...
    async def get_status(self, provider_run_ref: ProviderRunRef) -> ProviderObservation: ...
    async def cancel(self, provider_run_ref: ProviderRunRef) -> ProviderObservation: ...
    async def fetch_result(
        self, provider_run_ref: ProviderRunRef
    ) -> ProviderResultManifest: ...
```

首个 Adapter 是 Jenkins，但 Port 只使用 TAP 术语。Jenkins Job、Agent Label、参数 Schema、Runner Image 与 Pipeline/Shared Library digest 必须来自已发布 Target Revision allowlist。提交响应不确定时进入 `SUBMIT_UNKNOWN`，通过 `reconcile_submission(target, submission_key)` 查询 `NOT_FOUND | QUEUED | STARTED` 及可选 Provider Run Ref；`NOT_FOUND` 在对账期限前不等于“可安全重试”，未证明未接收前禁止盲目再次提交。

Environment、Execution Target 与 Credential Binding Profile 均以不可变 Revision 管理。Validation bootstrap 从受控服务端配置创建唯一验证配置；`list → verify connection → callback nonce handshake → enable` 全部成功后才可被 Run 引用。浏览器只能选择已启用 Revision，不能提交 Jenkins URL、Job、Agent label、Pipeline ref 或 Secret；每次修改/轮换创建新 Revision，旧 Run 继续引用原快照。

Jenkins 在任何目标副作用前必须用单次 claim token 认领 Run/Attempt/Revision/Bundle digest。Callback HMAC-SHA256 的签名材料是以下对象按 RFC 8785 JSON Canonicalization Scheme 编码后的 UTF-8 字节；`bodySha256` 使用请求原始 body 的小写十六进制 SHA-256：

```json
{
  "signatureVersion": "tap-jenkins-callback-v1",
  "httpMethod": "POST",
  "requestPath": "/api/v1/provider-callbacks/jenkins/{target_id}",
  "keyId": "...",
  "targetId": "...",
  "runId": "...",
  "submissionKey": "{run_id}:1",
  "timestamp": "RFC3339 UTC",
  "nonce": "...",
  "bodySha256": "..."
}
```

Callback 和 polling 进入同一个幂等 Normalizer，终态不可回退。Jenkins `SUCCESS` 不自动等于 `PASSED`。

## 8. Evidence 与 Test Plan 结果投影

Action Result 先归并为 Automation BDD Step Result，再通过 Run 冻结的 mapping 投影为 Test Plan Step Result。Test Plan 和 LCA 展示同一个 Run ID，不复制运行事实。

- Run 开始时有关联：同一 Run 进入 Automation Run History 和 Test Plan Execution History。
- Run 开始时无关联：不产生 Test Plan 投影；事后关联不追投。
- 后续解除关联：保留旧 Run 中冻结的 Test Plan/link/mapping 快照。
- `PASSED + INCOMPLETE`：可以显示测试结果为 Passed，但必须同时明确 Evidence incomplete，不能显示完整验收成功。

Evidence 至少支持 JUnit、Playwright trace、失败截图、可选视频以及脱敏 console/network/pipeline log。每个对象校验 content type、大小与 SHA-256；Artifact Gateway 使用绑定 run/attempt/object/operation/max-bytes/TTL 的短期单次授权，不暴露 MinIO locator。

## 9. HTTP 与错误语义

Project 业务 API 均位于：

```text
/api/v1/projects/{project_id}/...
```

创建和副作用命令使用 `Idempotency-Key`；相同 key/相同 canonical request 返回原结果，相同 key/不同请求返回 `409 idempotency-conflict`。Draft 更新使用 `If-Match`。Problem Details 遵循 RFC 9457，`type` 使用稳定绝对 URI `https://tap.example/problems/{slug}`；产品部署可把 host 迁移到拥有的文档域，但同一 API major 内 slug、HTTP status 与语义不得变化。每个响应扩展字段至少包含 `correlationId`、`retryable`，工作流失败再包含闭集 `failureStage`；不得返回 Secret、Provider 原始错误或内部堆栈。

| slug                             | HTTP | retryable | 状态语义                                           |
| -------------------------------- | ---: | --------- | -------------------------------------------------- |
| `scope-mismatch`                 |  403 | false     | 路径 Project 与服务端 Scope 不一致                 |
| `authorization-denied`           |  403 | false     | 当前 Actor/Scope 不允许该动作                      |
| `idempotency-conflict`           |  409 | false     | 同 key 对应不同 canonical request                  |
| `revision-conflict`              |  409 | false     | `If-Match` 或不可变 Revision 冲突                  |
| `association-conflict`           |  409 | false     | Test Plan/Automation 已被另一端占用                |
| `automation-mapping-required`    |  409 | false     | Published Revision 缺兼容 Step Mapping             |
| `answer-unavailable`             |  503 | true      | 回答依赖暂时不可用，未写伪成功                     |
| `graph-unavailable`              |  503 | true      | 显式 Graph 查询失败；普通问答按 Graph 状态诚实降级 |
| `execution-provider-unavailable` |  503 | true      | Provider 暂不可用；不得把未知提交解释为可安全重试  |

客户端只能依据 `type`、`status`、`retryable` 和公开状态转换决定交互；`detail` 是可展示说明，不是可解析控制字段。相同 `correlationId` 必须贯穿 HTTP、Audit、Outbox、Worker 与 Provider Observation。

Provider callback、Artifact claim 和 Recorder stream 使用独立非 Project 浏览器 API，但 token 必须绑定原 Project/Actor/Resource；不能以路径形状绕过 Policy。

## 10. 生成与验证

- Pydantic Backend DTO 是 HTTP Schema 源；`scripts/export_contracts.py` 生成 `contracts/openapi/api.json` 和 SSE Schema，再由 Web 脚本生成 TypeScript。
- 公开 DTO、SSE union、数据库 constraint、Provider Port 与 Web type 必须由 contract tests 保持一致。
- Fake Adapter 只用于确定性行为和故障测试；V1/V2/V3/V4/V5 出口分别要求真实模型/Milvus、Graph 抽取、测试设计质量、Recorder/回放和 Jenkins E2E 证据。
- 任何实现完成声明必须指向对应里程碑的测试与 Review；接口存在、页面可见或单次 fake 成功都不是里程碑完成。
