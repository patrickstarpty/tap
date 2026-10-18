---
id: ADR-032
status: accepted
date: 2026-10-12
supersedes: []
superseded-by: []
related-rfcs: []
---

# ADR-032：Skill 包存放边界与工具注册表

## 背景

Skills/Agents（V1 能力 4）引入导入 `SKILL.md`/Skill 包、组装 Agent（系统提示词 + Skills + 工具白名单 + 默认模型）与在对话中使用它们。这跨越 `ai`（资产 Revision、工具注册表）、`knowledge`（Skill 指令进入回答提示词组装，`modules/knowledge/adapters/litellm.py`）与 `chat`（Turn 输入冻结 Skill 选择）三个模块，且涉及"是否执行导入包内代码"这类安全边界，需要跨模块记录的决策，而不只是模块内文档。详细设计见[自定义与导入 Skills/Agents 设计](../superpowers/specs/2026-10-11-skills-agents-design.md)；迁移为 `0035_skills_agents_authoring`（`down_revision` 为 `0034_turn_retry`）。

## 决策

- **包作为不可变修订存对象存储**：导入或新建的 Skill 包按 `skills/{skill_id}/{revision_id}/{relative_path}` 原样写入 MinIO，写入后不再修改；元数据（描述、来源、指令正文、已附加的参考文本、frontmatter JSON、文件清单）写入 MySQL（`skill_revision`、`skill_revision_file`）。重新导入同名 Skill 或编辑，生成新修订，旧修订保留，二者共用同一服务端生成的修订 id 命名空间与存储路径规则。暂存导入（`POST /ai/skills/imports`）使用独立的 staging 前缀，24 小时后由清理任务删除，与已提交修订的路径不混用。
- **包内代码永不执行**：`scripts/`、`assets/` 等非 `SKILL.md`/`references/` 文件只存档、可下载，不在任何路径下被解析执行；`SKILL.md` 正文与被引用的 `references/` 文本文件按 64 KB 上限附加进提示词，附加内容仅作为纯文本字符串拼接，不触发任何脚本解释器。
- **`allowed-tools` frontmatter 字段不授予任何执行权限**：该字段与 `license`、`compatibility`、`metadata` 等其他可选字段一样原样保存在 frontmatter JSON 中并在界面展示，但代码路径中没有任何地方读取它来放宽或收紧工具调用——工具权限只由 Agent 的工具白名单（取自工具注册表）决定。
- **工具注册表为代码定义的边界，V1 两项内置**：`src/tap/modules/ai/domain/tools.py` 的 `TOOL_REGISTRY` 替代此前在领域模型、Turn 输入、生成治理、模型网关四处硬编码的 `knowledge.search`/`knowledge.answer` 两值闭集；`known_tool_ids()` 当前仍恰好返回这两个内置工具，两者在当前执行路径中都是必需（`required=True`），Agent 表单工具区只读展示，不可取消。注册表引入的是"查表"这一层，不是新增工具；新工具通过在注册表中新增 `ToolDefinition` 接入，无需再改四处硬编码。
- **MCP 不在 V1**：仓库中没有 MCP server 登记或 MCP 工具调用代码；工具注册表只登记代码内定义的内置工具，MCP 的接入推迟到 V1 之后决策。

## 考虑过的方案

- 包内代码按白名单沙箱执行（如只跑 `scripts/` 下声明的脏活脚本）：被否决——V1 没有工具调用循环，模型无法按需触发脚本，沙箱执行的安全面与审计成本在当前范围内不成比例;脚本只存档可下载已满足"可审阅不可信代码"的需求。
- 把 `allowed-tools` frontmatter 映射为实际工具授权：被否决——会让 Skill 包（通常来自外部仓库、未经审阅）直接影响 Agent 可调用的工具范围，架空 Agent 级白名单这一唯一的权限入口；记录但不生效保留了展示价值且不引入信任边界。
- 在 V1 范围内登记 MCP server 并把其工具并入注册表：被否决——2026-10-11 决定略过，注册表先以代码定义的内置工具稳定接口，MCP 的网络面、认证与工具发现机制留给单独设计。

## 后果

- 新增工具只需在 `TOOL_REGISTRY` 增补 `ToolDefinition`，四个消费点（领域模型、Turn 输入、生成治理、模型网关）保持不变地查表；`GET /ai/tools` 直接暴露注册表供前端渲染。
- 导入的 Skill 包无论来源是否可信，都不会因为声明 `allowed-tools` 或携带 `scripts/` 而获得额外执行能力或工具权限，降低了"导入第三方 Skill"这一动作本身的风险面。
- Skill 与 Agent 的历史修订可追溯：`GET /ai/skills/{revisionId}`、`GET /ai/agents/{revisionId}` 返回修订详情，Turn 冻结的是具体修订 id 而非可变的"当前版本"，回放与重试复用冻结修订。
- MCP 接入仍是未决项，留给后续单独的 ADR 或设计处理；当前文档（本 ADR、架构、路线图）须一致说明"MCP 不在 V1"而非留空造成歧义。
