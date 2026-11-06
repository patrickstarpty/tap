# OpenCode 编码工作流接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将整个编码任务交给 OpenCode CLI 的 Orchestrator 与专业 Agent，同时保留 TAP 的版本、控制、恢复和实时运行记录。

**Architecture:** `CodingRuntime` 隔离 provider 差异；OpenCode 适配器生成任务配置快照、管理工作区及进程、归一化经过验证的事件。LangGraph 只管理外层生命周期和原生/编码交接，不另行规划 OpenCode 内部工作。

**Tech Stack:** 现有 Python/MySQL/MinIO/LiteLLM、OpenCode CLI、受控本机运行容器、阶段 1 运行 API 与 UI。

**Spec:** [整体设计](../specs/2026-11-06-agent-workflows-and-test-loop-design.md) §3–7；[总计划](2026-11-06-agent-workflows-and-test-loop.md)；[能力验证门禁](2026-11-06-opencode-capability-validation.md)。

## 阶段 0 实际门禁与 adapter 输入

[OpenCode 阶段 0](2026-11-06-opencode-capability-validation.md)尚未实施；当前 PATH 未找到 CLI，未固定版本/模型组合，八项 required 全部 unverified。旧 Copilot 确定性探针不能作为新 provider 证据。阶段 1 可独立推进，阶段 2 必须等真实能力门禁与运行合同就绪。

在 Task 2.1–2.3 编码前，从 OpenCode 真实脱敏 fixture 映射 CodingCapabilities、CodingHandle、ProviderEventKey、normalize_event 与控制/恢复回执：CLI/模型/配置摘要、主/子 session 与 Agent 身份、父子关系、终态、稳定事件键或源游标、LiteLLM 全调用关联、指令接收/应用及会话/工作区/进程恢复。缺可信计数、执行端限额、真实子实例或恢复证据时保持阻塞；不能复制旧字段。主模型、小模型及标题/压缩等辅助路径均不得绕过 LiteLLM。

批处理候选为 opencode run JSON；实时候选为 opencode serve 的回环 HTTP/SSE。阶段 0 固定实际接口；HTTP 认证、监听与配置隔离是运行边界的一部分。外层 LangGraph 不重复规划 OpenCode 内部子任务。

## Global Constraints

- 阶段 0 的 required 能力必须有真实通过证据；未通过只能继续诊断，不能发布编码入口。
- 所有编码路径模型调用由 OpenCode → LiteLLM 承担；不在外层增加第二个模型 Orchestrator。
- 全部已启用且授权 Agent 的文件启动前生成，无 `tap-` 前缀；任务中冻结配置，禁止依赖热加载。
- 暂停/恢复须同时核对会话、工作区和进程；恢复失败保持 recovering/awaiting_input，不能重新跑完并冒充原地恢复。
- 用户定向要求由 OpenCode Orchestrator 处理；不支持即时输入时采用实测通过的安全边界续接，并在 UI 告知何时生效。
- 只实现 OpenCode；Claude Code/Codex 仅保留 provider ID 和共同端口，调用时明确 unsupported。
- 任务停止、预算超限和授权撤销由执行端落实，不能只写进提示词。

## 文件结构

| 新文件 | 职责 |
| --- | --- |
| `B/modules/workflows/ports/coding.py` | provider-neutral 生命周期与能力合同 |
| `B/modules/workflows/domain/coding.py` | session/manifest/能力模型 |
| `B/modules/workflows/adapters/opencode_profiles.py` | Agent/Skill/工具映射与快照 |
| `B/modules/workflows/adapters/opencode_process.py` | 受控进程和事件读取 |
| `B/modules/workflows/adapters/opencode_events.py` | 已验证 provider 事件归一化 |
| `B/modules/workflows/adapters/opencode_runtime.py` | port 的实现 |
| `B/modules/workflows/adapters/workspaces.py` | 工作副本、快照、校验与制品导出 |
| `B/modules/workflows/application/handoff.py` | 原生/编码唯一调度权交接 |
| `deploy/coding/Dockerfile`、`build-inputs.json` | 固定 CLI 与运行基础镜像输入 |

### Task 2.1：provider 合同与不可变配置快照

**Files:** Create 上表 coding domain/port、profiles/workspaces；Create `BT/unit/workflows/test_opencode_profiles.py`、`BT/contract/test_coding_runtime.py`；Create `docs/superpowers/specs/2026-11-06-opencode-workflow-runtime-design.md`、`docs/decisions/2026-11-06-adr-035-agent-runtime-model-boundary.md`。

**Interfaces:** `CodingCapabilities(provider, version, event_schema, input_mode: Literal["live","boundary"], resume_mode: Literal["session_and_workspace"], child_identity: bool, parallel: bool)`；`CodingLaunch(task_id, attempt, manifest_digest, workspace_ref, objective, input_refs)`；`CodingHandle(session_id, task_id, attempt, workspace_ref, provider_version)`。端口：

```python
class CodingRuntime(Protocol):
    async def capabilities(self) -> CodingCapabilities: ...
    async def start(self, launch: CodingLaunch) -> CodingHandle: ...
    def events(self, handle: CodingHandle, cursor: str | None) -> AsyncIterator[dict[str, object]]: ...
    async def submit(self, handle: CodingHandle, command: ControlCommand) -> dict[str, object]: ...
    async def interrupt(self, handle: CodingHandle, command: ControlCommand) -> dict[str, object]: ...
    async def reconcile(self, handle: CodingHandle) -> dict[str, object]: ...
    async def resume(self, handle: CodingHandle, manifest_digest: str) -> CodingHandle: ...
    async def artifacts(self, handle: CodingHandle) -> tuple[dict[str, object], ...]: ...
```

其中 `ControlCommand` 来自阶段 1；字典结果以阶段 1 `WorkflowEvent`/receipt 契约校验，不允许 adapter 任意扩展终态。`FrozenManifest` 明确含 schema_version、task_id、agent_revision_ids、skill_revision_ids、tool_mapping、model_route、CLI version、文件摘要和输入资产引用；不含模型密钥。

- [ ] 固化阶段设计与 ADR-035：native 继续使用 ModelGateway，OpenCode 是显式外部模型运行边界；记录费用/权限/事件补偿责任、单一 Orchestrator 规则、生命周期合同和阶段 0 证据。ADR 仅一个 status 字段，不提前宣称实测能力。
- [ ] 写失败测试：profile 全量且授权过滤、保留原配置版本、稳定文件名、同显示名不同 ID 不冲突、个人配置不覆盖、未知工具不变为全权限、任何 secret 不进入 manifest。定义 `agent_filename(agent_id: str) -> str`：

```python
import pytest
from tap.modules.workflows.adapters.opencode_profiles import agent_filename

def test_stable_agent_filename_cannot_escape_workspace():
    assert agent_filename("orchestrator") == "orchestrator.md"
    assert agent_filename("sdet") == "sdet.md"
    with pytest.raises(ValueError):
        agent_filename("../sdet")
```

- [ ] `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/unit/workflows/test_opencode_profiles.py apps/tap-ai-backend/tests/contract/test_coding_runtime.py -q` 先失败；实现 profile exporter 与 runtime port。描述用于选择、正文用于执行；始终向 Orchestrator 传入用户显式选择与全部获准专业 Agent 的简短名册。`.opencode/agents/orchestrator.md` 使用 mode: primary，是 Tapper 的内部启动配置，单独冻结系统根身份及修订，不加入用户可选目录或子 Agent 委派候选；专业 Agent 使用 mode: subagent；增加根 profile 存在但 TAP 目录与委派候选不含根身份的测试。文件包含 description/model/permission，正文为指令，不使用 Copilot strict-tools-list。
- [ ] 映射工具权限必须复核 OpenCode permission 与 task 规则、内置 Agent 和显式 @ 的实测边界；禁止直接把平台工具 ID 写成 OpenCode 工具名。Skill 指令按冻结修订映射，包内脚本不自动执行。主/子模型、small_model、标题/摘要/压缩等辅助调用全部保持 LiteLLM 路由，无兼容模型时拒绝启动。
- [ ] 新增只读 frozen manifest 校验和独立工作副本；并行写任务不得无协调覆盖同一目录。窄测通过，提交 `feat: define coding runtime and frozen agent profiles`。

### Task 2.2：进程、真实事件与持久 session

**Files:** Create `opencode_process.py/opencode_events.py/opencode_runtime.py`、AI migration `0040_coding_sessions.py`、coding Dockerfile/build-inputs；Modify workflows schema/mysql、runtime composition、`.env.example`；Create `BT/unit/workflows/test_opencode_events.py`、`BT/integration/test_opencode_process.py`。

**Interfaces:** `coding_session` 保存 task/node/attempt、provider session ID、CLI/模型/manifest 版本、工作区引用、启动幂等键、最后事件游标、进程/容器标识。`ProviderEventKey(session_id, provider_event_id)` 唯一；无稳定 provider event ID 时使用阶段 0 已验证的源游标构造，不能哈希自然语言文本误合并不同调用。`normalize_event(raw: dict[str, object], context: CodingHandle) -> tuple[dict[str, object], ...]` 输出阶段 1 契约。

- [ ] 将阶段 0 的真实脱敏 fixture 加入回放测试：主/子 Agent、工具开始结束、并行、失败、重复、乱序和流截断；断言父子身份一致。定义 `complete_from_evidence(process_exit: int | None, provider_terminal: str | None, artifacts_verified: bool) -> str`：

```python
from tap.modules.workflows.adapters.opencode_events import complete_from_evidence

def test_cli_exit_zero_is_not_delivery_success():
    assert complete_from_evidence(0, None, True) == "recovering"
    assert complete_from_evidence(0, "succeeded", False) == "failed"
    assert complete_from_evidence(0, "succeeded", True) == "succeeded"
```

- [ ] 单测先失败，再实现事件映射；上面的进程退出断言仅适用于 run 捕获，serve 是长活服务，须以实际 session 终态与产物校验判定，不能等待服务退出。仅真正的 provider 子实例创建 Agent 节点，模型文本不能创建完成事件。未知事件保存受限脱敏摘要，必需身份丢失则 capability_error。
- [ ] 受控容器内固定 OpenCode 版本，挂载任务工作副本和专属配置目录；只给该任务模型凭据和所需网络，不传宿主个人目录、Docker socket 或不相关凭据。worker 在容器外通过 argv 管理生命周期；任务代码不获得进程管理权限。容器化后重跑阶段 0 核心场景。
- [ ] 启动前持久化幂等 session intent，容器命名/标签使用任务+attempt，响应丢失先查既有容器再决定是否启动；无 shell 字符串拼接。状态提交后推送，stderr 不能当模型输出或成功证明。
- [ ] 新设置 `TAP_CODING_ENABLED=false`、`TAP_CODING_PROVIDER=opencode`；CLI 版本和模型组合来自已验证 manifest，BYOK key 只由已有机密配置注入。启动时验证能力，不支持 provider 明确报错。
- [ ] stub/fixture 测试通过后执行真实 BYOK 单/双 Agent，关联 LiteLLM 使用记录，缺用量为 unknown，父子不重复计费；提交 `feat: persist opencode sessions and lifecycle events`。

### Task 2.3：定向输入、暂停与会话工作区恢复

**Files:** Modify runtime/process/workspaces、application recovery；Create `BT/integration/test_coding_controls.py`、`BT/integration/test_coding_recovery.py`、`BT/smoke/test_opencode_workflow_runtime.py`。

**Interfaces:** `resume_allowed(expected_digest: str, actual_digest: str, process_state: str) -> bool`；控制使用阶段 1 command ID 与 expected_plan_revision。工作区快照清单保存路径、字节长度、内容 digest，排除凭据/缓存，拒绝符号链接逃逸；对象存储引用在数据库结算。

- [ ] 写失败测试：

```python
from tap.modules.workflows.adapters.workspaces import resume_allowed

def test_resume_requires_matching_workspace_and_confirmed_stop():
    assert not resume_allowed("sha256:a", "sha256:b", "stopped")
    assert not resume_allowed("sha256:a", "sha256:a", "unknown")
    assert resume_allowed("sha256:a", "sha256:a", "stopped")
```

- [ ] 先运行新 unit/integration 文件确认失败；实现暂停请求→停止新委派→达到安全边界→确认进程状态→保存快照→paused。暂停期间累计成本/调用次数不清零，恢复另建 attempt 并保留 lineage。
- [ ] 定向 steer 传给 OpenCode Orchestrator，保留 target 子实例；根据能力矩阵采用 live 或 boundary。服务端接收输入不等于业务已应用，abort 请求成功不等于子实例与工具已停止，必须核对真实状态。自然语言“收到”只记 pending；实际变更计划/输入并获得执行端关联确认才 applied。不支持的即时交付不得 UI 假装完成。
- [ ] 故障矩阵：worker 被杀但 CLI 存活、CLI 被杀且产物已写、最终事件丢失、对象上传成功但 DB 失败、旧 worker 回调、重复 pause/cancel、resume 后权限撤销。均先 reconcile，不重复启动或发布；长任务新增 Agent 仅在新阶段加载快照，原结果保留。
- [ ] 真模型 smoke 使用双 opt-in，至少覆盖边界 steer、暂停恢复、崩溃核对和文件不重复修改；UI 明确显示恢复级别及失败原因。
- [ ] 提交 `feat: recover coding sessions with acknowledged controls`。

### Task 2.4：原生交接、统一入口与验收

**Files:** Create application `handoff.py`、`BT/integration/test_workflow_handoff.py`；Modify 阶段 1 routing/worker、F workflow 节点详情；Create `apps/tap-ai-frontend/tests/e2e/coding-workflows.spec.ts`；Modify guide、架构和产品路线图。

**Interfaces:** `handoff(scope, task_id, expected_plan_revision, target: Literal["native","opencode"], input_refs, idempotency_key) -> str` 返回新运行段 ID；`transfer_owner(current: str, expected: str, target: str) -> str` 比较失败拒绝。任务 ID 不变，运行段绑定自身 provider、配置、输入和输出版本。

- [ ] 写失败测试：

```python
import pytest
from tap.modules.workflows.application.handoff import transfer_owner

def test_old_orchestrator_cannot_take_ownership_back():
    assert transfer_owner("native", "native", "opencode") == "opencode"
    with pytest.raises(ValueError):
        transfer_owner("opencode", "native", "opencode")
```

- [ ] `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests/integration/test_workflow_handoff.py -q` 先失败；实现冻结输入→CAS 调度权→新运行段→结果汇聚。外层只执行状态与授权，不调用模型重新分配 OpenCode 子任务。
- [ ] 验证纯编码请求直接进 OpenCode；非编码途中产生编码需求可交接；同任务简单问答保留当前 Orchestrator；执行已发布制品只调用执行器。显式 @ 保持目标 Agent，停用/缺能力说明原因。
- [ ] UI 合并两种 runtime 的真实事件；任务详情可看内部 Orchestrator 的运行段与 provider，不生成独立可调用档案；CLI 原生步骤、平台工具、人工审核明确区分，provider 身份不能伪装成 TAP 内部调用。
- [ ] 隔离浏览器重放证明 UI 契约；真实 OpenCode 独立验收证明能力。运行总计划检查，更新现状架构/guide/路线图，提交 `feat: connect native and opencode workflow handoffs`。
