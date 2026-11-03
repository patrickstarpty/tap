# Skills/Agents 设计

日期：2026-11-03。V1 子项目 6（能力 4），见 [V1 总纲](../plans/2026-09-29-v1-roadmap.md)。为 [图谱脉络分析](2026-11-03-graph-context-analysis-design.md) 提供工具注册表与有界工具循环；为 V2 [主动 Agent](2026-09-30-tapper-proactive-agent-design.md) 提供 Agent 与 Skill 基础。

## 现状

- 已有不可变修订：`AiAgentRevision`（系统提示词、输出 schema、工具白名单）与 `SkillRevision`（指令模板、适用任务），表 `ai_agent*`、`skill*`（迁移 0011），HTTP 只读列表与详情。
- 工具白名单在领域层、会话层、LiteLLM 网关三处硬编码为 `{"knowledge.search","knowledge.answer"}`。
- 会话可选一个 Agent 修订与若干 Skill 修订，指令按治理规则拼入提示词；模型由会话 `model_alias` 决定。
- 无创建、导入接口；前端 Agents/Skills 页面在 `localStorage` 编辑草稿并下载 Markdown；无 MCP 依赖。

## 目标

1. 导入 `SKILL.md` 生成不可变 Skill 修订；导入不执行任何内含代码。
2. Agent = 系统提示词 + Skills + 工具白名单 + 模型，创建与编辑都产生新的不可变修订。
3. 工具注册表替代硬编码白名单。
4. 可注册 MCP server，其工具可加入 Agent 白名单，并在对话中被调用。
5. 有界工具循环：选中 Agent 的对话按白名单调用工具。

## 非目标

- 执行 Skill 附带脚本、Skill 市场、修订审批流（V1 单人本机环境，创建即启用；审批随 V4 身份设计）。
- stdio 传输的 MCP server（会在服务端启动任意进程）；只支持 Streamable HTTP。
- 有写入副作用的内置工具。

## 1. 工具注册表

### 领域（`ai/domain/tools.py`）

`ToolDescriptor`：

| 字段 | 说明 |
| --- | --- |
| `tool_id` | 内置为 `knowledge.search` 这类点分名；MCP 为 `mcp.{server_slug}.{tool_name}` |
| `display_name`、`description` | 发给模型的说明，长度有上限 |
| `input_schema` | JSON Schema（对象，深度与大小有上限） |
| `effect` | `read` / `propose` / `external` |
| `origin` | `builtin` / `mcp`，MCP 附 `server_id` |
| `enabled` | MCP 工具随 server 禁用 |

规则：

- `knowledge.answer` 为保留能力名（治理回答），不作为可调用工具暴露给模型，但仍可出现在白名单中以兼容现有修订。
- 系统 Agent（V2 主动剧本）只能包含 `read`/`propose` 工具；用户 Agent 可包含 `external`。

### 端口与实现

- `ToolRegistry` 端口：`list(scope)`、`get(scope, tool_id)`、`require_allowlist(scope, tool_ids)`（未知、禁用或超出允许效果即拒绝）。
- 内置工具注册：`knowledge.search`（检索已发布知识，返回带标签证据）、`graph.neighbors`、`graph.path`（子项目 5）、`insights.query`（已存在的 Insights 查询能力包装）。每个内置工具实现 `ToolExecutor.invoke(scope, arguments) -> ToolResult`，参数先按 schema 校验。
- 组合注册表 = 内置 + MySQL 中启用的 MCP 工具。
- 三处硬编码集合改为：领域层只校验格式（点分名、数量 ≤ 32），应用层用 `ToolRegistry.require_allowlist` 校验存在性与效果；LiteLLM 网关不再校验工具名集合。

## 2. Skill 导入

### 接口

`POST /api/v1/projects/{project_id}/ai/skills/imports`，`multipart/form-data` 单文件 `SKILL.md`（UTF-8、≤ 64 KiB），权限 `ai.skills.write`。

### 解析（`ai/application/skill_import.py`）

- 必须有 YAML front matter（`---` 包围），用安全加载器（只允许标量、列表、映射）。
- 必填：`name`（小写 kebab-case，≤ 64）、`description`（≤ 1024）。可选：`allowed-tools`（列表，记录为 `requested_tools`，仅信息展示，不自动授予）、`metadata`（映射，原样保存）。其他键保留在 `front_matter_json`。
- 正文为指令模板（非空）。正文中的代码块按纯文本保存；不解析、不下载链接、不执行任何内容，不接受压缩包或附加文件。
- `content_digest` = 原文件字节 SHA-256。同一项目内同名 Skill 若最新修订摘要相同则幂等返回该修订；否则在该 Skill 下追加修订号 +1 的新修订；新名称创建新 Skill。
- 新修订 `applicable_tasks={"knowledge.answer"}`，状态 `enabled`，`origin="import"`。

### 其他接口

- `GET .../ai/skills/{revision_id}/content`：返回原始 `SKILL.md`。
- `POST .../ai/skills`：从表单（name、description、instructions）创建，服务端生成规范 `SKILL.md` 后走同一导入路径（前端现有编辑器改为调用它）。
- `POST .../ai/skills/revisions/{revision_id}/status`：`enabled` / `disabled`。

## 3. Agent

### 修订字段补充（迁移）

`ai_agent_revision` 新增 `model_alias`（可空，空表示用会话模型）、`description`；新表 `ai_agent_revision_skill(revision_id, skill_revision_id, position)`。

### 接口（权限 `ai.agents.write`）

- `POST .../ai/agents`：`{displayName, description, systemInstruction, skillRevisionIds[], toolAllowlist[], modelAlias?}` → 新 Agent 与修订 1。
- `POST .../ai/agents/{asset_id}/revisions`：同结构 → 修订号 +1，`adopted_from_revision_id` 指向上一修订。
- `POST .../ai/agents/revisions/{revision_id}/status`。
- 校验：Skill 修订存在且启用；工具经注册表校验；`modelAlias` 在模型目录中且支持结构化输出（使用工具时还需支持工具调用）；输出 schema 沿用平台默认回答 schema。

### 会话运行

- 选中 Agent 时：模型 = Agent 的 `model_alias`（有值时覆盖会话模型），Skills = Agent 绑定的 Skills ∪ 会话额外选择的 Skills（去重、保序），工具 = Agent 白名单。这些值按现有方式快照进 `TurnInput`，历史 Turn 不受后续修订影响。

## 4. MCP

### 依赖与数据

- 新依赖 `mcp`（官方 Python SDK，锁定版本），只用 Streamable HTTP 客户端。
- 表 `ai_mcp_server`：`server_id`、项目范围、`slug`（kebab）、`display_name`、`url`、`auth_header_env`（可空，存环境变量名而非密钥）、`enabled`、`last_synced_at`、`last_error`。
- 表 `ai_mcp_tool`：`server_id`、`tool_name`、`description`、`input_schema_json`、`effect`、`enabled`。

### 接口（权限 `ai.tools.manage`）

- `POST .../ai/mcp-servers` 注册并立即同步；`POST .../ai/mcp-servers/{id}/sync`；`PATCH` 启停；`GET` 列表含工具。
- `GET .../ai/tools`：注册表全量（内置 + MCP），供 Agent 编辑器选择（权限 `ai.agents.read`）。

### 安全

- URL 必须为 `http(s)`；默认只允许 loopback 主机，`TAP_MCP_ALLOWED_HOSTS` 显式追加；拒绝重定向到未允许主机。
- 同步：`initialize` + `tools/list`，超时 10s，工具数 ≤ 64；`readOnlyHint=true` 映射 `read`，否则 `external`。
- 调用：超时 30s，结果文本截断到 16 KiB 并标记；结果作为不可信数据进入模型上下文（与检索证据相同的包裹说明）。
- 每次调用写 span `tool.mcp.call`（server、tool、耗时、状态，参数与结果按现有脱敏后记录摘要）。

## 5. 有界工具循环

### 网关

`ModelGateway` 新增 `chat_with_tools(request, tools) -> ToolTurn`：OpenAI 兼容 `tools`/`tool_choice=auto`，返回文本或 `tool_calls`（id、名称、参数 JSON）；记录 `model_call`。确定性假网关按脚本返回工具调用，供测试。

### 循环（`ai/application/tool_loop.py`）

LangGraph 子图 `model → tools → model`，状态含消息、步数与已用 token：

- 上限：工具步数 ≤ 4、单步并行调用 ≤ 3、总 token 预算按模型上下文的 50%；达到上限时强制最后一次无工具调用以产出回答。
- 只暴露白名单中且可调用的工具；模型请求的未知工具返回错误结果而不执行。
- 工具结果附稳定标签（如 `T1`），`knowledge.search` 返回的证据进入引用候选集，最终回答仍使用现有结构化回答 schema 与引用校验，证据范围 = 初始检索证据 ∪ 工具返回证据。
- 每步 span `tool.loop.step`、每次调用 span `tool.{tool_id}`。

### 进入条件

Turn 选中的 Agent 白名单含除 `knowledge.answer` 外的可调用工具时，`execute` 节点在检索后进入工具循环；否则保持现有一次性回答路径。

## 6. 前端

`apps/tap-ai-frontend`：

- Skills 页：列表来自后端；“导入 SKILL.md”（文件选择，展示解析出的名称、描述、请求工具与警告后确认）；“新建”编辑器调用创建接口；详情展示修订历史与原文；启停。
- Agents 页：列表；编辑器字段为名称、描述、系统提示词、Skills 多选、工具多选（按来源分组、显示效果标签）、模型选择；保存即生成新修订。
- 工具与 MCP：Agents 页内“工具”页签，列出内置工具与 MCP server，可注册、同步、启停。
- 会话选择器沿用；回答调用链已展示工具 span。
- 删除 `localStorage` 草稿逻辑与 Markdown 下载以外的本地模拟数据（下载保留，改为下载服务端原文）。
- `/prototype` 设计基准同步：Skills 加导入流程，Agent 编辑器加工具与模型，加工具/MCP 页签；fixture 数据，比较前后截图，核对导航与跨模块旅程。

## 7. 权限

新增动作 `ai.skills.write`、`ai.agents.write`、`ai.tools.manage`（资源 `ai`），加入验证身份策略；本机演示身份拥有全部权限。

## 8. 测试与验收

- 单元：SKILL.md 解析（缺 front matter、非法名称、超大、YAML 标签注入被拒、代码块原样保存）、工具描述校验、注册表白名单校验、循环上限与未知工具处理。
- 契约：导入/创建/修订/启停接口、工具列表、MCP 注册（用进程内假 MCP Streamable HTTP server）、会话使用 Agent 的模型与合并 Skills、工具循环调用 MCP 工具并在回答中出现其结果。
- MySQL 集成：修订不可变与修订号递增、幂等导入、Agent-Skill 关联。
- 安全：非 loopback MCP URL 被拒；导入内容含 `<script>`/shell 代码时仅存储不执行（断言无子进程与网络调用）。
- 前端：导入流程、Agent 编辑器、工具页签。
- `make check`、`make test`、`make demo-e2e` 通过；更新 `docs/architecture.md` 与 `docs/guides/` 新增 Skills/Agents/MCP 使用指南。
