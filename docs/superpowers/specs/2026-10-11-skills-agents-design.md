# 自定义与导入 Skills/Agents 设计

日期：2026-10-11。归属 [V1 总纲](../plans/2026-09-29-v1-roadmap.md) 能力 4"Skills/Agents"（子项目 5）。背景评审见 [V1 交付物评审](../../reviews/2026-10-06-v1-delivery-review.md) 第 2.4 节与第 6 节。

## 背景

- 后端只有只读的 `GET /ai/agents`、`GET /ai/skills`。资产来自代码中的种子数据（Knowledge agent、Citation skill），修订 id 写死在代码里，没有任何写路径。
- `skill_revision` 只存指令摘要，指令正文来自种子代码。
- 工具白名单在领域模型、Turn 输入、生成治理、模型网关四处硬编码为 `knowledge.search`、`knowledge.answer` 两值闭集。`knowledge.answer` 是选用 Agent 的前提；`knowledge.search` 不影响任何执行。白名单只写进模型调用的 metadata，没有工具调用。
- Agent 系统提示词和 Skill 指令已经真实进入回答提示词，并参与 Turn 冻结与摘要治理。
- 模型按轮选择（`modelAlias`），没有 Agent 级默认模型。
- 前端 Agents/Skills 页只有 localStorage 本地草稿，"Create agent draft"无法在对话中使用。
- 仓库中没有 SKILL.md 解析器，也没有 MCP 代码。

## 目标

- 用户可以导入主流 Agent Skills 目录结构的 Skill 包，也可以在界面新建 Skill；两者都成为不可变修订。
- 用户可以组装 Agent：系统提示词、绑定的 Skills、工具白名单、默认模型。
- 工具白名单改由工具注册表定义，替代四处硬编码。
- 在对话中选用 Agent 与 Skills，回答可追溯到所用的每个修订。
- 业务验证：导入至少一个真实外部 Skill，并在对话中使用（真实模型）。

## 非目标

- MCP server 登记与 MCP 工具（2026-10-11 决定略过）。
- 工具调用循环、执行 Skill 包内的脚本或代码。
- 删除资产；只提供停用。
- 自定义 Skill 接入测试方案生成与推荐问题；V1 只承诺问答链路。
- 在界面暴露 Agent 的输出格式（`output_schema_json`）。

## 1. Skill 包的结构、解析与存储

### 1.1 支持的目录结构

```text
my-skill/
├── SKILL.md          必需。YAML frontmatter + Markdown 正文
├── scripts/          可选。只存档，不执行
├── references/       可选。参考文档（.md/.txt）
└── assets/           可选。模板、图片等资源，只存档
```

frontmatter：

| 字段 | 规则 |
| --- | --- |
| `name` | 必需。小写字母、数字、连字符，最长 64 字符 |
| `description` | 必需。最长 1 024 字符 |
| `license`、`compatibility`、`metadata`、`allowed-tools` | 可选，原样保存并展示 |
| 其他字段 | 原样保留，不报错 |

`allowed-tools` 只记录，不授予任何工具权限；工具权限只由 Agent 白名单决定。

### 1.2 上传形式

- 单个 SKILL.md；`.zip`；浏览器目录上传的多个文件；粘贴 SKILL.md 文本。
- zip 根目录可以直接是 SKILL.md，也可以是唯一的一层顶级目录。
- 一个包含多个 Skill（如 `skills/a/SKILL.md`、`skills/b/SKILL.md`）：解析时全部列出，用户勾选要导入的项，每项成为独立 Skill。

### 1.3 内容如何进入提示词

- SKILL.md 正文作为 Skill 指令。
- `references/` 下被正文以相对路径引用的文本文件，按引用顺序附加在指令之后；单个 Skill 附加总量上限 64 KB，超出的文件不附加并在详情中标出。未被引用的参考文件只存档。
- `scripts/`、`assets/` 与其他文件不进入提示词，在详情中标注"已保存，不执行"，可下载查看。

原因：V1 没有工具循环，模型无法按需读取文件，只能预先附加。

### 1.4 安全校验

导入时任一项不通过即拒绝，并返回原因。

| 项 | 规则 |
| --- | --- |
| 压缩包大小 | ≤ 20 MB |
| 解压后总大小 | ≤ 50 MB，边解压边计量 |
| 文件数 | ≤ 500 |
| 单个 SKILL.md | ≤ 256 KB，UTF-8 |
| 路径 | 拒绝绝对路径、`..`、符号链接、设备文件 |
| 名称 | `name` 不合规拒绝；同一项目重名时提示"作为新修订导入" |

### 1.5 存储

- 对象存储（MinIO）：包按 `skills/{skill_id}/{revision_id}/<相对路径的 sha256>` 存放（相对路径可能含对象标识不允许的字符，故用其哈希代替原样拼接；原始相对路径保留在 `skill_revision_file.relative_path`），写入后不修改。单个 SKILL.md 导入同样按此结构存放。
- MySQL（新迁移，编号接在实施时最新迁移之后）：
  - `skill_revision` 增加：描述、来源（`imported`、`authored`、`built_in`）、指令正文、附加的参考文本、frontmatter JSON、包摘要。指令摘要覆盖正文与已附加的参考文本。
  - 新表 `skill_revision_file`：相对路径、大小、sha256、类别（`skill`、`reference`、`script`、`asset`、`other`）、是否附加进提示词。
- 修订 id 由服务端生成。重新导入同名 Skill 或在界面编辑，都生成新修订，旧修订保留。
- 界面新建的 Skill 生成一份只含 SKILL.md 的包，走同一解析器与存储。

## 2. Agent 组装与工具注册表

### 2.1 Agent

每次保存生成一个不可变修订。

| 字段 | 规则 |
| --- | --- |
| 名称、描述 | 名称 ≤ 128 字符，同项目不重名；描述 ≤ 1 024 字符 |
| 系统提示词 | 必填，≤ 32 KB |
| Skills | 多选，绑定到具体 Skill 修订 |
| 工具白名单 | 取自工具注册表 |
| 默认模型 | 可选，取自现有模型目录 |

- Skill 绑定固定到修订。Skill 出新修订时，Agent 详情提示"有新版本"，"升级"生成 Agent 新修订。
- 对话中，Agent 绑定的 Skills 自动生效；用户可额外勾选 Skills，取并集去重。
- 默认模型在选中 Agent 时预填到输入框，可逐轮覆盖；该模型从目录消失时 Agent 仍可用，输入框回落到全局默认模型并提示一次。
- 内置 Knowledge agent、Citation skill 保留为"内置"，只读，可"复制为自定义"。
- 自定义资产可停用或启用；停用后不出现在选择列表，历史对话照常显示。
- 导入的 Skill 默认适用"知识问答"，表单可改选"测试方案生成""自动化生成"（沿用现有三种任务）。
- 自定义 Agent 统一使用 Tapper 默认回答格式。

### 2.2 工具注册表

- 新后端模块，在代码中定义工具：标识、中英文名称、描述、是否必需、适用任务。四处两值闭集改为查询注册表。
- V1 只有内置 `knowledge.search`、`knowledge.answer`，不建表。
- Agent 表单的工具区只读显示"内置工具：知识检索、知识回答（始终启用）"，因为两者在当前执行路径中都不可关闭；注册表有更多工具后再变为可勾选。
- 图谱关系分析仍按问题意图触发，不受白名单控制。

## 3. API 与契约

前缀 `/api/v1/projects/{project_id}/ai/`，契约由 `make contracts` 生成。

### 3.1 Skill 导入

| 方法与路径 | 作用 |
| --- | --- |
| `POST /ai/skills/imports` | multipart（SKILL.md、zip、多文件）或 JSON 粘贴文本。完成校验与解析，包暂存到对象存储 staging 区，返回 `importId` 与预览：每个 Skill 的路径、frontmatter、文件清单与类别、附加情况、告警、重名情况 |
| `POST /ai/skills/imports/{importId}/commit` | 提交勾选的 Skill，可指定 `asNewRevisionOf: skillId`。同一 `importId` 重复提交返回同一结果 |

staging 记录 24 小时 TTL，由下一次 `stage_import` 调用时的惰性清理回收（无独立的定时清理任务）。

### 3.2 Skill 管理

| 方法与路径 | 作用 |
| --- | --- |
| `POST /ai/skills` | 表单新建：名称、描述、指令、适用任务 |
| `POST /ai/skills/{skillId}/revisions` | 编辑生成新修订，带 `baseRevisionId`，非最新返回 409 |
| `POST /ai/skills/{skillId}/disable`、`/enable` | 停用、启用 |
| `GET /ai/skills`、`GET /ai/skills/{revisionId}` | 扩展字段：描述、来源、内置或自定义、状态、是否最新；详情含文件清单 |
| `GET /ai/skills/{skillId}/revisions` | 修订历史 |
| `GET /ai/skills/{revisionId}/files/{path}` | 下载包内文件，`Content-Disposition: attachment`，`text/plain` 或 `application/octet-stream` |

### 3.3 Agent 与工具

- `POST /ai/agents`、`POST /ai/agents/{agentId}/revisions`（带 `baseRevisionId`）、停用、启用、修订历史。
- `GET /ai/agents*` 扩展字段：系统提示词、绑定的 Skill 修订、默认模型、工具白名单、"绑定的 Skill 有新版本"。
- `GET /ai/tools` 返回注册表。

### 3.4 对话、兼容性与权限

- 发送消息的请求不变。服务端合并 Agent 绑定的 Skills 与请求中的 Skills，写入 Turn 冻结输入。`modelAlias` 仍必填，由前端用默认模型预填。
- 现有 `GET` 字段全部保留；`toolAllowlist` 从两值枚举改为字符串，由注册表校验。
- 新增权限 `ai.skills.write`、`ai.agents.write`，本地演示身份默认拥有。

### 3.5 错误码

| 错误 | 状态码 | 错误码 |
| --- | --- | --- |
| 包过大 | 413 | `skill_package_too_large` |
| 路径非法 | 400 | `skill_path_rejected` |
| frontmatter 不合规 | 400 | `skill_frontmatter_invalid` |
| 包中未找到 SKILL.md | 400 | `skill_not_found_in_package` |
| 重名 | 409 | `skill_name_conflict` |
| 修订冲突 | 409 | `revision_conflict` |
| 暂存已过期 | 410 | `import_expired` |
| 单轮 Skill 超出预算 | 400 | `skill_budget_exceeded` |

## 4. 对话链路与提示词组装

### 4.1 顺序

1. Tapper 平台指令（依据、引用、拒答规则），始终第一。
2. Agent 系统提示词。
3. Skill 指令：Agent 绑定的在前，额外勾选的在后；每个 Skill 用带名称与修订号的分隔块包住，附加的参考文件在块内。
4. 检索证据、关系上下文、问题（不变）。

平台指令写明：Agent 与 Skill 指令不能改变依据与引用规则，冲突时以平台规则为准。

### 4.2 预算

单轮所有 Skill 文本（含附加参考）合计 ≤ 96 KB。超出时前端发送前提示占用最大的 Skill，服务端返回 `skill_budget_exceeded`。不做静默截断。

### 4.3 冻结与追溯

- Turn 冻结 Agent 修订、合并后的 Skill 修订列表及其摘要。
- "本次回答使用的来源与设置"列出 Agent 与各 Skill 修订，可打开当时的内容。
- trace 的回答 span 记录 Agent 修订 id、Skill 修订 id 列表与 Skill 文本 token 估算。

### 4.4 生效范围

- 普通问答与关系问答都生效。
- 不适用于"知识问答"的 Skill 在输入框置灰并说明。
- Agent 绑定的 Skill 被停用后，发送时跳过并提示一次，Agent 详情给出警告。
- 测试方案生成与推荐问题保持现状。

## 5. 前端

### 5.1 Skills 页

- 列表：筛选"全部、内置、自定义、已停用"；卡片显示名称、描述、来源、适用任务、修订号、更新时间；主按钮"导入 Skill"，次按钮"新建 Skill"。
- 导入对话框：第一步拖入或选择 SKILL.md、zip、文件夹，或粘贴文本；第二步预览，每个 Skill 一行带勾选，可展开 frontmatter 与文件树，文件按类别标注（指令、参考·已附加/未附加、脚本·已保存不执行、资源），告警单列，重名可选"作为新修订导入"或"跳过"。
- 详情抽屉：名称、修订切换；操作为在对话中使用、编辑、停用或启用，内置项为"复制为自定义"；标签页为指令、文件、元数据、修订历史。
- 新建或编辑表单：名称、描述、适用任务、指令（Markdown 预览）；实时显示生成的 SKILL.md 与大小。

### 5.2 Agents 页

- 列表结构同 Skills 页，主按钮"新建 Agent"。
- 表单：名称、描述、系统提示词（大小计数）、Skills 可搜索多选（显示修订，不适用项置灰）、工具只读、默认模型下拉。
- 详情：绑定的 Skills 与"有新版本"升级、修订历史、"在对话中使用"。

### 5.3 对话输入框

- 选中 Agent 后，绑定的 Skills 以锁定标签显示"来自 Agent"，额外 Skills 可移除；模型预填为默认模型。
- Skill 合计接近或超过预算时，发送按钮上方提示。

### 5.4 本地草稿迁移与通用要求

- 去掉 localStorage 草稿机制。检测到旧草稿时显示一次横幅"发现本地草稿 N 个，导入到项目？"，导入后清除本地数据，也可忽略。
- 中英文案齐全，空状态有引导，产品界面不放演示开关或实现说明。

## 6. 测试与验收

### 6.1 后端

- 单元：SKILL.md 解析；包校验（压缩炸弹、路径穿越、符号链接、文件数、单层顶级目录、多 Skill）；参考附加与 64 KB 上限；摘要确定性；工具注册表替换后行为不变；Agent 组装（固定修订、合并去重、跳过停用、96 KB 拒绝）；提示词顺序与平台规则优先。恶意包在测试中生成，不提交二进制。
- 契约：新接口与错误码；现有 `GET` 契约测试不改即通过。
- 集成（隔离 MySQL 与对象存储夹具，一次一个文件）：导入到 Turn 冻结的完整链路、提交幂等、409、staging 清理、下载响应头。

### 6.2 前端

Vitest 覆盖导入两步、文件分类、重名选择、两个表单校验、锁定标签与预算提示、草稿迁移横幅、中英切换。

### 6.3 E2E 与截图

- `make demo-e2e` 新增 journey：导入仓库内自编示例 Skill 包（含 `scripts/`、`references/`）→ 新建绑定它的 Agent → 提问 → "来源与设置"出现该 Skill 修订 → 文件页脚本标注"不执行"。
- 确定性模型网关增加测试钩子：提示词含约定标记时回答带回该标记，证明 Skill 指令进入提示词。
- `ui:capture` 新增 Skills 列表、导入预览、Skill 详情、Agent 表单截图。

### 6.4 业务验证

从公开 Skill 仓库选一个许可证允许、带 `scripts/` 的 Skill，在演示环境导入，用真实模型对话确认 Skill 生效且引用规则未被覆盖，结果写入验证报告。

### 6.5 文档与门禁

- 更新 `docs/architecture.md`、V1 总纲能力 4、产品路线图子项目 5。
- 新增 ADR-032：Skill 包作为不可变修订存放在对象存储、包内代码永不执行、工具注册表边界。
- `make check`、`make test`、`make demo-e2e` 全部通过。
