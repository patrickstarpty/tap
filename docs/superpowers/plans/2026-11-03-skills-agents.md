# Skills/Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 交付 V1 能力 4 的工程完成项：导入 `SKILL.md` 生成不可变修订、Agent 组合（提示词 + Skills + 工具 + 模型）、工具注册表、MCP server 注册与调用、有界工具循环。

**Architecture:** `ai` 模块新增工具注册表（内置 + MySQL 中的 MCP 工具），替代三处硬编码白名单；Skill 导入与 Agent 修订写入现有不可变修订表（扩展列与关联表）；MCP 只用 Streamable HTTP 客户端；网关新增 `chat_with_tools`，`execute` 节点在 Agent 白名单含可调用工具时进入 LangGraph 工具循环子图；前端 Agents/Skills 页面改为后端驱动。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、Alembic、LangGraph、`mcp` 官方 Python SDK、PyYAML（安全加载）、pytest；React/TypeScript/Vitest；Playwright 截图。

**Spec:** [Skills/Agents 设计](../specs/2026-11-03-skills-agents-design.md)

## Global Constraints

- 迁移从 `0030` 起顺序编号。
- 工具 ID：内置点分名（`knowledge.search`）；MCP 为 `mcp.{server_slug}.{tool_name}`；单个白名单 ≤ 32 项；`knowledge.answer` 为保留能力名，不暴露给模型调用。
- `effect ∈ {read, propose, external}`；系统 Agent 只允许 `read`/`propose`。
- `SKILL.md`：UTF-8、≤ 64 KiB；必须有 YAML front matter（安全加载，只允许标量/列表/映射）；`name` 小写 kebab-case ≤ 64；`description` ≤ 1024；`allowed-tools` 仅记录为 `requested_tools`，不授予；正文非空；不执行、不下载、不接受压缩包。`content_digest` = 原文件字节 SHA-256；同名同摘要幂等。
- MCP：只支持 Streamable HTTP；URL 必须 `http(s)`，默认只允许 loopback 主机，`TAP_MCP_ALLOWED_HOSTS`（逗号分隔）追加；拒绝重定向到未允许主机；同步超时 10s、工具数 ≤ 64；调用超时 30s、结果文本截断 16 KiB 并标记；`readOnlyHint=true` → `read`，否则 `external`；`auth_header_env` 存环境变量名不存密钥。
- 工具循环：工具步数 ≤ 4、单步并行调用 ≤ 3、token 预算为模型上下文的 50%；到上限强制最后一次无工具调用。
- 新权限：`ai.skills.write`、`ai.agents.write`、`ai.tools.manage`（资源 `ai`）。
- 契约变更后 `make contracts` 并提交生成物；UI 改动同步 `/prototype` 并比较前后截图。
- 提交：Conventional Commit 小写祈使句，带 `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`。

---

### Task 1: 工具注册表替代硬编码白名单

**Files:**
- Create: `apps/tap-ai-backend/src/tap/modules/ai/domain/tools.py`、`modules/ai/application/tool_registry.py`
- Modify: `modules/ai/domain/assets.py`（`_DOMAIN_TOOLS` 检查改为格式校验）、`modules/ai/domain/models.py`（约 50–81 行同类检查）、`modules/ai/adapters/litellm.py`（约 261–274 行：去掉工具名集合校验，保留“治理回答需含 `knowledge.answer`”规则）、`modules/chat/domain/conversations.py`（约 93–145、190–195 行）、`interfaces/http/routes/conversations.py`（约 90–116 行解析 Agent 时用注册表校验）、`entrypoints/tapper_runtime.py`（装配注册表）
- Test: `tests/unit/ai/test_tool_registry.py`、相关现有测试更新

**Interfaces:**
- Produces:
  ```python
  class ToolEffect(StrEnum): READ = "read"; PROPOSE = "propose"; EXTERNAL = "external"
  class ToolOrigin(StrEnum): BUILTIN = "builtin"; MCP = "mcp"
  @dataclass(frozen=True, slots=True)
  class ToolDescriptor:
      tool_id: str; display_name: str; description: str
      input_schema: Mapping[str, object]; effect: ToolEffect; origin: ToolOrigin
      server_id: str | None = None; enabled: bool = True
      @property
      def callable(self) -> bool  # False 仅对保留名 knowledge.answer
  RESERVED_TOOLS = frozenset({"knowledge.answer"})
  def validate_tool_id(value: str) -> str      # ^[a-z][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*){1,3}$，≤ 128
  def validate_allowlist(values: Iterable[str]) -> frozenset[str]  # 格式 + 数量 ≤ 32
  @dataclass(frozen=True)
  class ToolResult: content: str; truncated: bool = False; evidence: tuple[object, ...] = (); is_error: bool = False
  class ToolExecutor(Protocol):
      async def invoke(self, scope: ProjectScopeContext, arguments: Mapping[str, object]) -> ToolResult: ...
  class ToolRegistry:
      def __init__(self, builtins: Mapping[str, tuple[ToolDescriptor, ToolExecutor | None]], mcp: "McpToolSource | None" = None) -> None: ...
      async def list(self, scope) -> tuple[ToolDescriptor, ...]
      async def get(self, scope, tool_id: str) -> ToolDescriptor   # 未知抛 ToolRejected
      async def require_allowlist(self, scope, tool_ids: Iterable[str], *, system: bool = False) -> frozenset[str]
      async def executor(self, scope, tool_id: str) -> ToolExecutor
  class ToolRejected(ValueError): ...
  class McpToolSource(Protocol):   # Task 4 实现
      async def descriptors(self, scope) -> tuple[ToolDescriptor, ...]: ...
      async def executor(self, scope, tool_id: str) -> ToolExecutor: ...
  ```
  本任务内置注册：`knowledge.answer`（保留，不可调用，无执行器）、`knowledge.search`（`read`，执行器在 Task 6 实现，本任务注册描述符与 schema `{"query": string ≤ 500, "limit": int 1..8}`）。

- [x] **Step 1: 失败测试**：`validate_tool_id` 接受 `knowledge.search`、`mcp.jira.search_issues`，拒绝大写、空段、超长；`validate_allowlist` 拒绝 33 项；注册表 `require_allowlist` 对未知/禁用工具抛 `ToolRejected`，`system=True` 时拒绝 `external`；现有修订（白名单 `{"knowledge.search","knowledge.answer"}`）仍可加载。
- [x] **Step 2: 实现并替换三处硬编码**；`grep -rn '"knowledge.search", "knowledge.answer"\|_DOMAIN_TOOLS' apps/tap-ai-backend/src` 不再有集合成员校验（领域层只做格式校验）。
- [x] **Step 3: 运行** `uv run --project apps/tap-ai-backend pytest apps/tap-ai-backend/tests -q -k "asset or conversation or gateway or tool"`。
- [x] **Step 4: 提交** `feat: resolve agent tools through a registry`

### Task 2: Skill 导入、创建与启停

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0030_skill_import.py`（`skill_revision` 增 `origin VARCHAR(16) NOT NULL DEFAULT 'seed'`、`description VARCHAR(1024) NULL`、`requested_tools_json JSON NULL`、`front_matter_json JSON NULL`、`source_markdown LONGTEXT NULL`）
- Create: `modules/ai/application/skill_import.py`
- Modify: `modules/ai/domain/assets.py`（`SkillRevision` 增字段）、`modules/ai/adapters/mysql.py`（`import_skill`、`set_skill_status`、`skill_source`）、`interfaces/http/routes/ai_assets.py`（新端点）、`modules/access/adapters/validation.py`（三项新权限）、`pyproject.toml`（若无 `pyyaml` 则添加并 `uv lock`）
- Run: `make contracts`
- Test: `tests/unit/ai/test_skill_import.py`、`tests/contract/test_ai_asset_http.py`（追加）、`tests/integration/test_skill_import_mysql.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ParsedSkill: name: str; description: str; instructions: str; requested_tools: tuple[str, ...]; front_matter: Mapping[str, object]; content_digest: str; source_markdown: str; warnings: tuple[str, ...]
  class SkillImportRejected(ValueError): code: str   # skill-too-large / skill-not-utf8 / skill-front-matter-missing / skill-front-matter-invalid / skill-name-invalid / skill-description-invalid / skill-body-empty
  def parse_skill_markdown(raw: bytes) -> ParsedSkill
  def render_skill_markdown(name: str, description: str, instructions: str) -> bytes
  class SkillImportService:
      async def import_markdown(self, scope, raw: bytes) -> tuple[SkillRevision, bool]  # (revision, created)
      async def create(self, scope, *, name, description, instructions) -> tuple[SkillRevision, bool]
      async def set_status(self, scope, revision_id: str, status: AssetRevisionStatus) -> SkillRevision
      async def source(self, scope, revision_id: str) -> bytes
  ```
  HTTP（前缀同现有 `ai_assets` 路由）：`POST /ai/skills/imports`（multipart 字段 `file`，权限 `ai.skills.write`，201 新建 / 200 幂等，响应 `SkillRevisionView` + `warnings`）、`POST /ai/skills`（JSON `{name, description, instructions}`，同上）、`GET /ai/skills/{revision_id}/content`（`text/markdown`，`ai.skills.read`）、`POST /ai/skills/revisions/{revision_id}/status`（`{status}`，`ai.skills.write`）。错误用现有 problem details，类型码即 `SkillImportRejected.code`。`allowed-tools` 中注册表未知的名称进入 `warnings`。

- [x] **Step 1: 失败测试（解析）**：合法文件；缺 front matter；YAML 含 `!!python/object` 标签被拒；名称 `My Skill` 被拒；65 KiB 被拒；非 UTF-8 被拒；正文含 ```` ```bash rm -rf / ``` ```` 与 `<script>` 原样保存；`allowed-tools: [knowledge.search, Bash]` → `requested_tools` 两项、`warnings` 含 `Bash`；`render_skill_markdown` 往返解析一致。
- [x] **Step 2: 失败测试（MySQL 集成）**：首次导入创建 Skill 与修订 1；同内容再导入返回同修订且不新增行；改正文再导入得修订 2 且 `adopted_from_revision_id` 为修订 1；同名不同项目互不影响；停用后 `resolve_skill` 拒绝。
- [x] **Step 3: 失败测试（契约）**：四个端点状态码、无写权限 403、错误类型码；导入过程中断言未创建子进程（monkeypatch `asyncio.create_subprocess_exec`、`subprocess.Popen` 抛错）且无外部 HTTP 调用。
- [x] **Step 4: 实现**（解析用 `yaml.safe_load`，并拒绝非 `dict` 顶层与非字符串键）；`make contracts`。
- [x] **Step 5: 运行** 上述测试与 `-k "asset or skill"`。
- [x] **Step 6: 提交** `feat: import skill markdown as immutable revisions`

### Task 3: Agent 创建、修订与会话运行

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0031_agent_composition.py`（`ai_agent_revision` 增 `model_alias VARCHAR(128) NULL`、`description VARCHAR(1024) NULL`、`origin VARCHAR(16) NOT NULL DEFAULT 'seed'`；新表 `ai_agent_revision_skill(project/enterprise scope 列同既有表, revision_id, skill_revision_id, position, PK(revision_id, position))`）
- Create: `modules/ai/application/agent_authoring.py`
- Modify: `modules/ai/domain/assets.py`（`AiAgentRevision` 增 `model_alias`、`description`、`skill_revision_ids: tuple[str, ...]`）、`modules/ai/adapters/mysql.py`、`interfaces/http/routes/ai_assets.py`、`interfaces/http/routes/conversations.py`（约 171–207 行：选中 Agent 时模型与 Skills 合并规则）
- Run: `make contracts`
- Test: `tests/unit/ai/test_agent_authoring.py`、`tests/contract/test_ai_asset_http.py`、`tests/contract/test_conversation_http.py`（追加）、`tests/integration/test_agent_authoring_mysql.py`

**Interfaces:**
- Consumes: `ToolRegistry.require_allowlist`（Task 1）、`SkillRevision`（Task 2）、模型目录（`modules/ai/application/catalog.py`）。
- Produces:
  ```python
  @dataclass(frozen=True)
  class AgentDraft: display_name: str; description: str; system_instruction: str; skill_revision_ids: tuple[str, ...]; tool_allowlist: frozenset[str]; model_alias: str | None
  class AgentAuthoringService:
      async def create(self, scope, draft: AgentDraft) -> AiAgentRevision
      async def revise(self, scope, asset_id: str, draft: AgentDraft) -> AiAgentRevision
      async def set_status(self, scope, revision_id: str, status: AssetRevisionStatus) -> AiAgentRevision
  ```
  HTTP（`ai.agents.write`）：`POST /ai/agents`、`POST /ai/agents/{asset_id}/revisions`、`POST /ai/agents/revisions/{revision_id}/status`；请求 `{displayName, description, systemInstruction, skillRevisionIds[], toolAllowlist[], modelAlias?}`。校验：显示名 1..128、系统提示词 1..16000、Skills ≤ 16 且均启用、工具经注册表、`modelAlias` 在目录中且支持结构化输出（白名单含可调用工具时还需目录标记支持工具调用；目录无该标记时以 `structured` 能力代替并在报告中说明）。输出 schema 用平台默认回答 schema（与现有种子 Agent 相同摘要）。
  会话：选中 Agent 时 `model_alias = agent.model_alias or 请求的 model_alias`；Skills = Agent 绑定 ∪ 请求的额外 Skills（去重保序，Agent 在前）；其余快照逻辑不变。

- [x] **Step 1: 失败测试**：服务校验（未知 Skill、停用 Skill、未知工具、未知模型、超长提示词）；修订号递增与 `adopted_from_revision_id`；会话选 Agent 后 `TurnInput` 的模型、Skills 顺序与白名单符合规则；历史 Turn 快照不因新修订改变（MySQL 集成）。
- [x] **Step 2: 实现；`make contracts`。**
- [x] **Step 3: 运行** `-k "agent or asset or conversation"`（含 MySQL 集成）。
- [x] **Step 4: 提交** `feat: author agents from instructions, skills, tools and a model`

### Task 4: MCP server 注册、同步与调用

**Files:**
- Create: `apps/tap-ai-backend/migrations/versions/0032_mcp_servers.py`（表 `ai_mcp_server`、`ai_mcp_tool`，列见 spec §4，均带项目范围列与外键）
- Create: `modules/ai/domain/mcp.py`（URL/主机校验、slug 校验）、`modules/ai/adapters/mcp_client.py`（Streamable HTTP：`list_tools`、`call_tool`）、`modules/ai/adapters/mysql_mcp.py`（仓储 + `McpToolSource` 实现）、`modules/ai/application/mcp_servers.py`
- Modify: `apps/tap-ai-backend/pyproject.toml`（`mcp` 锁定版本，`uv lock`）、`interfaces/http/routes/ai_assets.py` 或新 `routes/ai_tools.py`（`/ai/mcp-servers`、`/ai/tools`）、`entrypoints/tapper_runtime.py`（`ToolRegistry(..., mcp=MysqlMcpToolSource(...))`）
- Run: `make contracts`
- Test: `tests/unit/ai/test_mcp_policy.py`、`tests/contract/test_mcp_http.py`（进程内假 MCP server：用 `mcp` SDK 的 FastMCP Streamable HTTP app 挂在 `httpx.ASGITransport` 或本地 uvicorn 回环端口）、`tests/integration/test_mcp_servers_mysql.py`

**Interfaces:**
- Consumes: `ToolDescriptor`、`ToolExecutor`、`ToolResult`、`McpToolSource`（Task 1）。
- Produces:
  ```python
  def allowed_mcp_url(url: str, *, extra_hosts: frozenset[str]) -> str   # 拒绝抛 McpRejected("mcp-url-not-allowed")
  class McpClient(Protocol):
      async def list_tools(self, url: str, headers: Mapping[str, str]) -> tuple[McpToolSpec, ...]
      async def call_tool(self, url: str, headers: Mapping[str, str], name: str, arguments: Mapping[str, object]) -> ToolResult
  @dataclass(frozen=True)
  class McpToolSpec: name: str; description: str; input_schema: Mapping[str, object]; read_only: bool
  class McpServerService:
      async def register(self, scope, *, slug, display_name, url, auth_header_env: str | None) -> McpServerView
      async def sync(self, scope, server_id) -> McpServerView
      async def set_enabled(self, scope, server_id, enabled: bool) -> McpServerView
      async def list(self, scope) -> tuple[McpServerView, ...]
  ```
  HTTP（`ai.tools.manage`）：`POST /ai/mcp-servers`、`POST /ai/mcp-servers/{server_id}/sync`、`PATCH /ai/mcp-servers/{server_id}`（`{enabled}`）、`GET /ai/mcp-servers`；`GET /ai/tools`（`ai.agents.read`）返回注册表全量 `ToolView{toolId, displayName, description, effect, origin, serverId?, enabled, callable}`。
  鉴权头：`auth_header_env` 指向的环境变量值作为 `Authorization` 头（变量缺失时同步失败并记录 `last_error="mcp-auth-missing"`）。

- [x] **Step 1: 失败测试（策略）**：`http://127.0.0.1:9000/mcp` 允许；`http://example.com/mcp` 拒绝；`TAP_MCP_ALLOWED_HOSTS=mcp.internal` 后允许；`file://`、`ftp://` 拒绝；slug 校验。
- [x] **Step 2: 失败测试（契约，假 MCP server 暴露 `echo`（readOnlyHint）与 `create_ticket`）**：注册后 `GET /ai/tools` 出现 `mcp.{slug}.echo`（`read`）与 `mcp.{slug}.create_ticket`（`external`）；停用 server 后工具 `enabled=false` 且 `require_allowlist` 拒绝；调用 `echo` 返回内容；超过 16 KiB 结果被截断标记；服务端超时映射 `is_error`；工具数 65 时同步失败。
- [x] **Step 3: 失败测试（MySQL 集成）**：重新同步替换工具集；项目隔离。
- [x] **Step 4: 实现**；每次调用 span `tool.mcp.call`（属性 server、tool、耗时、状态）。
- [x] **Step 5: 运行** 上述测试；`make contracts`。
- [x] **Step 6: 提交** `feat: register mcp servers as agent tool sources`

### Task 5: 网关工具调用

**Files:**
- Modify: `modules/ai/ports/gateway.py`、`modules/ai/domain/models.py`、`modules/ai/adapters/litellm.py`、`src/tap/testing/deterministic_model_gateway.py`、`scripts/run-quality-kb-real.py`（`CapturingModelGateway` 同步实现新方法）
- Test: `tests/contract/test_litellm_model_gateway.py`（追加）

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class ToolSpec: name: str; description: str; parameters: Mapping[str, object]
  @dataclass(frozen=True, slots=True)
  class ToolCall: call_id: str; name: str; arguments: Mapping[str, object]
  @dataclass(frozen=True, slots=True)
  class ChatMessage: role: Literal["system","user","assistant","tool"]; content: str; tool_calls: tuple[ToolCall, ...] = (); tool_call_id: str | None = None
  @dataclass(frozen=True, slots=True)
  class ToolTurn: text: str | None; tool_calls: tuple[ToolCall, ...]; result: ModelResult
  ModelGateway.chat_with_tools(request: ModelRequest, messages: tuple[ChatMessage, ...], tools: tuple[ToolSpec, ...]) -> ToolTurn
  ```
  OpenAI 兼容 `tools=[{"type":"function","function":{...}}]`、`tool_choice="auto"`（`tools` 为空时不传）；工具名在线上用 `__` 替换 `.`（如 `knowledge__search`），解析时还原；参数 JSON 解析失败的调用以 `arguments={"_raw": ...}` 返回并由循环报错。记录 `model_call`（operation `chat`）与 span，同现有路径共享代码。确定性网关：构造参数 `tool_script: Sequence[ToolTurnScript] | None`，按顺序返回预设调用，脚本耗尽后返回文本。

- [x] **Step 1: 失败测试**：假 HTTP 返回 `tool_calls` 被解析并还原名称；返回文本无调用；畸形参数；`model_call` 记录一行；确定性脚本按序返回。
- [x] **Step 2: 实现。**
- [x] **Step 3: 运行** `-k gateway` 与 `make check` 的 mypy 部分（`uv run --project apps/tap-ai-backend mypy apps/tap-ai-backend/src/tap scripts/run-quality-kb-real.py`）。
- [x] **Step 4: 提交** `feat: let the model gateway request tool calls`

### Task 6: 有界工具循环与内置执行器

**Files:**
- Create: `modules/ai/application/tool_loop.py`、`modules/knowledge/application/search_tool.py`（`knowledge.search` 执行器，复用已授权检索并返回带标签证据）
- Modify: `entrypoints/tapper_generation_worker.py`（`execute` 节点：白名单含可调用工具时，检索后进入循环；证据并集交给现有回答路径）、`modules/knowledge/adapters/litellm.py` / 回答入口（接受附加证据）、`entrypoints/tapper_runtime.py`（注册执行器）
- Test: `tests/unit/ai/test_tool_loop.py`、`tests/contract/test_agent_tool_loop.py`

**Interfaces:**
- Consumes: `ToolRegistry.executor`、`ModelGateway.chat_with_tools`、`ToolResult.evidence`。
- Produces:
  ```python
  @dataclass(frozen=True)
  class ToolLoopLimits: max_steps: int = 4; max_parallel: int = 3; token_budget: int
  @dataclass(frozen=True)
  class ToolLoopOutcome: transcript: tuple[ChatMessage, ...]; evidence: tuple[object, ...]; steps: int; stopped_by: Literal["model","steps","budget"]; tool_labels: Mapping[str, str]
  class ToolLoop:
      def __init__(self, gateway: ModelGateway, registry: ToolRegistry, limits: ToolLoopLimits) -> None: ...
      async def run(self, scope, request: ModelRequest, messages: tuple[ChatMessage, ...], allowlist: frozenset[str]) -> ToolLoopOutcome
  ```
  LangGraph `StateGraph`：`model` → 条件边（有调用且未达上限 → `tools`，否则 END）→ `tools` → `model`；`recursion_limit = 2*max_steps+2`；每步 span `tool.loop.step`、每次调用 span `tool.{tool_id}`；未知/未授权工具返回 `ToolResult(is_error=True, content="tool-not-allowed")` 不执行；单步调用超出 3 个时只执行前 3 个、其余返回 `tool-call-limit`；工具结果以 `T1..Tn` 标签包裹为不可信数据。回答阶段：证据 = 初始检索证据 ∪ 工具证据（去重，≤ 20），走现有结构化回答与引用校验。

- [x] **Step 1: 失败测试（单元，确定性脚本）**：两步调用后给出文本；第 5 步被强制无工具；未知工具不执行；并行 4 个调用只执行 3 个；预算耗尽停止；工具抛异常转为 `is_error` 结果继续。
- [x] **Step 2: 失败测试（契约）**：选中白名单含 `mcp.{slug}.echo` 的 Agent 的会话，脚本调用 echo，回答成功、trace 中有 `tool.loop.step` 与 `tool.mcp.call` span；白名单只有 `knowledge.answer` 的会话路径与现状一致（不进入循环）。
- [x] **Step 3: 实现。**
- [x] **Step 4: 运行** `-k "tool or agent or conversation or generation"` 与全量后端一次。
- [x] **Step 5: 提交** `feat: run a bounded tool loop for agent turns`

### Task 7: 前端 Skills 页

**Files:**
- Modify: `apps/tap-ai-frontend/src/widgets/tap/workspace/CatalogWorkspace.tsx`（移除 `localStorage` 草稿，改用后端）、`apps/tap-ai-frontend/src/features/knowledge/api/aiAssets.ts`（新增导入/创建/原文/启停调用）、文案文件
- Test: 同目录 `*.test.tsx`

**Interfaces:**
- Consumes: Task 2 生成类型与端点。

- [x] **Step 1: 失败测试**：列表来自后端；“导入 SKILL.md”选文件 → 显示解析出的名称、描述、请求工具与警告 → 确认后列表出现；“新建”提交表单调用创建接口；详情显示修订历史与原文；启停切换；下载使用服务端原文；无 `localStorage` 读写（断言 `localStorage.setItem` 未被调用）。导入前预览：前端用同一规则本地解析 front matter 仅用于预览，提交以服务端结果为准。
- [x] **Step 2: 实现。**
- [x] **Step 3: 运行** `corepack pnpm --filter @tap/ai-frontend exec vitest run src/widgets/tap src/features/knowledge` 与 `run check`。
- [x] **Step 4: 提交** `feat: manage skills from the tap ai backend`

### Task 8: 前端 Agents 页与工具页签

**Files:**
- Modify: `CatalogWorkspace.tsx`（Agents 分支）或新建 `apps/tap-ai-frontend/src/widgets/tap/workspace/AgentEditor.tsx`、`ToolsPanel.tsx`；`aiAssets.ts`；文案
- Test: 同目录

**Interfaces:**
- Consumes: Task 3、Task 4 端点与类型。

- [x] **Step 1: 失败测试**：Agent 列表；编辑器字段（名称、描述、系统提示词、Skills 多选、工具多选按来源分组并显示效果标签、模型选择）保存后生成新修订；“工具”页签列出内置工具与 MCP server，可注册（slug、名称、URL、鉴权环境变量名）、同步、启停，错误码显示为可读文案（如 `mcp-url-not-allowed`）。
- [x] **Step 2: 实现。**
- [x] **Step 3: 运行** 同上。
- [x] **Step 4: 提交** `feat: compose agents with skills, tools and models in tap ai`

### Task 9: 原型同步

**Files:**
- Modify: `apps/web/src/widgets/tap/TapProductPrototype.tsx`（Skills 导入流程、Agent 编辑器的工具与模型、工具/MCP 页签；fixture 数据）
- Create: `docs/assets/v1-completion-2026-11-04/`（Agents/Skills 前后对照、编辑器、工具页与导入预览）

- [x] **Step 1: 截取改动前** `/prototype` Skills 与 Agents 屏（Playwright，`corepack pnpm --filter @tap/web run dev`）。
- [x] **Step 2: 实现**，不加演示开关或实现说明；保留全部既有模块与跨模块链接。
- [x] **Step 3: 截取改动后**，核对模块导航与跨模块旅程；`corepack pnpm --filter @tap/web run check` 与 `run test`。
- [x] **Step 4: 提交** `feat: show skill import and agent tools in the prototype`

### Task 10: 文档、指南与 E2E

**Files:**
- Create: `docs/guides/2026-11-04-skills-agents-mcp.md`（导入 Skill、组合 Agent、注册 MCP、安全边界）
- Modify: `docs/architecture.md`、`docs/superpowers/plans/2026-09-29-v1-roadmap.md`（能力 4 工程完成勾选；子项目 6 状态与 plan 链接）、`docs/superpowers/plans/2026-10-01-product-roadmap.md`、`apps/tap-ai-frontend/tests/e2e/`（新增或扩展：导入 Skill → 创建 Agent → 在会话中选择该 Agent 提问得到回答）、`.env.example`（`TAP_MCP_ALLOWED_HOSTS=`）

- [x] **Step 1: E2E 用例**并运行 `make demo-e2e`。
- [x] **Step 2: 验证** `make check`、`make test`。
- [x] **Step 3: 文档**，`git diff --check`。
- [x] **Step 4: 提交** `docs: record skills and agents completion`


## 2026-11-04 续接说明

- 新增独立的完整修订管理列表/详情；会话选择目录仍只列启用摘要。管理接口、禁用/重启/历史读取与项目范围契约通过。
- 前端移除本地草稿，导入/创建/修订/启停/历史/下载调用后端，保留原始文件字节；已停用或失效的依赖可以从 Agent 中移除。
- 工具参数使用完整 JSON Schema 2020-12 验证，远程 schema 引用不可获取；模型目录保留工具调用能力，创建与运行时都校验。
- 工具循环显式关闭父检查点继承，并覆盖同步持久化策略；真实 MySQL 嵌套回归验证执行成功且只保存外层检查点。当前 LangGraph 对无 saver 子图的显式策略给出预期告警，不影响外层持久化。
- `insights.query` 按 V1 总纲的 Test Insights 非目标移至 V2.2，避免把项目上下文错误当作指标委托身份；设计和产品路线图已同步。
- 最终门禁与前后对照见 [V1 增量验证记录](../../guides/2026-11-04-v1-completion-validation.md)。

最终验收：`make check`、完整 `make test`、完整隔离 `make demo-e2e` 通过。步骤由本轮综合交付提交统一收束；真实资料的业务验证不在这些勾选中。
